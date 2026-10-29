"""End-to-end run: fetch -> dedupe -> store -> claims -> verify -> draft -> store -> post or review.

Everything is saved to Supabase and logged in detail. In auto mode Green and Amber drafts are posted
(with a branded card); Red drafts, and everything in review mode, go to the owner on Telegram with buttons.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import ai, cleanup, duplicates, ingest, islamophobia, owner_feed, pipeline, scoring, severity, sources
from . import celebrity as celeb_mod
from .config import load_config
from .models import Article, Draft, Lane

log = logging.getLogger("haqq.run")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def known_keys(store, days: int) -> set[str]:
    """URLs already PROCESSED (normalised like Article.key). Stored-but-unprocessed articles are retried,
    so an AI outage never makes the backlog invisible."""
    if store is None:
        return set()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = store.select_raw("articles", {"select": "url,processed", "fetched_at": f"gte.{since}"})
        rows = [r for r in rows if r.get("processed")]
    except Exception as exc:  # noqa: BLE001 - e.g. `processed` column not created yet (db/migrations/003)
        log.warning("processed flag unavailable (%s); treating every stored article as known", type(exc).__name__)
        try:
            rows = store.select_raw("articles", {"select": "url", "fetched_at": f"gte.{since}"})
        except Exception:  # noqa: BLE001
            return set()
    return {r["url"].strip().lower().rstrip("/") for r in rows}


def mark_processed(store, article_id: int | None) -> None:
    if store is None or article_id is None:
        return
    try:
        store.update("articles", {"id": article_id}, {"processed": True})
    except Exception as exc:  # noqa: BLE001
        log.warning("could not mark article processed (%s): run db/migrations/003_articles_processed.sql", type(exc).__name__)


def claim_article(store, article_id: int | None) -> bool:
    """Atomically take an article so the fast track and the 4-hour run can never both process it."""
    if store is None or article_id is None:
        return True
    try:
        return store.claim_flag("articles", article_id, "processed", False, True)
    except AttributeError:
        return True
    except Exception as exc:  # noqa: BLE001 - e.g. the `processed` column is missing: behave as before
        log.warning("article claim unavailable (%s)", type(exc).__name__)
        return True


def release_article(store, article_id: int | None) -> None:
    if store is None or article_id is None:
        return
    try:
        store.update("articles", {"id": article_id}, {"processed": False})
    except Exception:  # noqa: BLE001
        pass


def is_paused(store) -> bool:
    """Owner kill switch (/pause in Telegram). Stored in settings.paused."""
    try:
        return store is not None and store.get_setting("paused", "false").strip().lower() == "true"
    except Exception:  # noqa: BLE001
        return False


def fast_posted_today(store) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    try:
        return len(store.select_raw("drafts", {"select": "id", "status": "eq.posted", "created_at": f"gte.{start}",
                                               "flags": "cs.{cat:breaking}"}))
    except Exception:  # noqa: BLE001
        return 0


def save_article(store, a: Article) -> int | None:
    if store is None:
        return None
    try:
        row = store.upsert("articles", {
            "url": a.url, "title": a.title, "summary": (a.summary or "")[:4000], "tier": a.tier.value,
            "language": a.language, "translated": a.translated, "published_at": _iso(a.published),
            "source_name": a.source_name}, "url")
        return row.get("id")
    except Exception as exc:  # noqa: BLE001
        log.warning("article not saved (%s): %s", type(exc).__name__, a.url)
        return None


def save_draft(store, article_id: int | None, outcome: pipeline.Outcome) -> int | None:
    d = outcome.draft
    if store is None or d is None or article_id is None:
        return None
    try:
        for c in d.claims:
            store.insert("claims", {"article_id": article_id, "text": c.text, "attribution": c.attribution,
                                    "status": c.status, "verified_by": c.verified_by})
        row = store.insert("drafts", {"article_id": article_id, "lane": d.lane.value, "status": "pending",
                                "headline": d.headline, "body": d.body, "sources": d.source_urls,
                                "label": d.label, "flags": d.packed_flags()})
        return row.get("id")
    except Exception as exc:  # noqa: BLE001
        log.warning("draft/claims not saved (%s)", type(exc).__name__)
        return None


def _done_platforms(store, draft_id) -> set[str]:
    try:
        rows = store.select_raw("posts", {"draft_id": f"eq.{draft_id}", "select": "platform"})
        return {r["platform"] for r in rows}
    except Exception:  # noqa: BLE001
        return set()


def _record(store, draft_id, results) -> list:
    done = [r for r in results if r.ok and not r.skipped]
    for r in done:
        store.insert("posts", {"draft_id": draft_id, "platform": r.platform, "platform_post_id": r.post_id})
    return done


def post_story_only(store, draft_id, draft, cfg: dict, stats: dict) -> None:
    """The feed post is blocked by a cap: the story still goes out (once), and the draft stays queued."""
    story_platforms = {p for p in ("facebook_story", "instagram_story") if cfg.get("enable", {}).get(p)}
    done = _done_platforms(store, draft_id)
    if not story_platforms - done:
        return
    try:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today = store.select_raw("posts", {"platform": "like.*_story", "posted_at": f"gte.{start}", "select": "id"})
        if len(today) >= cfg.get("stories", {}).get("max_per_day", 30):
            return
        results = pipeline.publish_draft(draft, "", dry_run=False, cfg=cfg, store=store, skip=done, story_only=True)
        stats["story_only"] = stats.get("story_only", 0) + len(_record(store, draft_id, results))
    except Exception as exc:  # noqa: BLE001
        log.warning("story-only post failed (%s)", type(exc).__name__)


def publish_ready(store, draft_id, draft, cfg: dict, stats: dict, final: bool = False, fast: bool = False) -> None:
    """Auto mode: post a Green/Amber draft live, record it, respect the daily cap. Needs the database."""
    from . import approvals

    if store is None or draft_id is None:
        stats["publish_blocked"] = stats.get("publish_blocked", 0) + 1
        owner_feed.error("auto-post", "draft not posted: it could not be saved, so the daily cap cannot be checked", cfg)
        return
    try:
        if is_paused(store):
            stats["paused"] = stats.get("paused", 0) + 1  # /pause in Telegram: the draft waits, nothing is lost
            return
        extra = cfg.get("fast_track", {}).get("extra_daily_posts", 0) if fast else 0
        if approvals._posted_today(store) >= cfg["limits"]["MAX_POSTS_PER_DAY"] + extra:
            stats["over_daily_limit"] = stats.get("over_daily_limit", 0) + 1
            post_story_only(store, draft_id, draft, cfg, stats)  # no feed post today, but the story still goes out
            return
        if not fast and stats.get("published", 0) >= cfg.get("run", {}).get("max_posts_per_run", 99):
            stats["queued"] = stats.get("queued", 0) + 1  # stays pending; posted by a later run, within 48h
            post_story_only(store, draft_id, draft, cfg, stats)
            return
        now = datetime.now(timezone.utc).isoformat()
        if not store.claim("drafts", draft_id, "pending", "approved", {"decided_at": now}):
            stats["already_claimed"] = stats.get("already_claimed", 0) + 1  # another job is posting it: never twice
            return
        results = pipeline.publish_draft(draft, "", dry_run=False, cfg=cfg, store=store, skip=_done_platforms(store, draft_id))
        done = _record(store, draft_id, results)
        feed_done = [r for r in done if not r.platform.endswith("_story")]
        store.update("drafts", {"id": draft_id}, {"status": "posted" if feed_done else ("skipped" if final else "pending")})
        stats["published"] = stats.get("published", 0) + (1 if feed_done else 0)
        if not feed_done:
            stats["publish_failed"] = stats.get("publish_failed", 0) + 1
    except Exception as exc:  # noqa: BLE001 - never lose the rest of the run
        log.exception("auto-post failed (%s)", type(exc).__name__)
        stats["publish_failed"] = stats.get("publish_failed", 0) + 1


def retry_unposted(store, cfg: dict, stats: dict, limit: int = 3) -> None:
    """Auto mode: one more try for Green/Amber drafts from the last 48h that never reached any platform."""
    if store is None or cfg.get("mode") != "auto":
        return
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=cfg["limits"]["INGEST_MAX_AGE_HOURS"])).isoformat()
        rows = store.select_raw("drafts", {"status": "eq.pending", "lane": "in.(green,amber)",
                                           "created_at": f"gte.{since}", "order": "created_at.asc", "limit": str(limit)})
        for row in rows:
            if store.select_raw("posts", {"draft_id": f"eq.{row['id']}", "platform": "not.like.*_story", "select": "id"}):
                continue
            draft = Draft.from_row(row)
            publish_ready(store, row["id"], draft, cfg, stats, final=True)
            stats["retried"] = stats.get("retried", 0) + 1
    except Exception as exc:  # noqa: BLE001
        log.warning("retry of unposted drafts skipped (%s)", type(exc).__name__)


def on_mission(a: Article, cfg: dict) -> bool:
    text = f"{a.title} {a.summary}".lower()
    return scoring.has_watch_topic(text, cfg) or any(k in text for k in cfg.get("mission_keywords", []))


def pillar(a: Article, cfg: dict) -> str:
    text = f"{a.title} {a.summary}".lower()
    if islamophobia.is_islamophobia(text):
        return "islamophobia"
    if any(k in text for k in ("palestin", "gaza", "west bank", "jerusalem", "hamas", "unrwa")):
        return "palestine"
    return ""


def select_for_run(fresh: list[Article], cfg: dict) -> list[Article]:
    """Choose what to draft: reserved slots for Palestine and Islamophobia first, then mission topics by severity,
    tier and recency, few per source, and a small off-mission quota."""
    limits = cfg.get("run", {})
    cap, per_source = limits.get("max_articles", 8), limits.get("max_per_source", 3)
    off_max = limits.get("off_mission_max", 2)
    epoch = datetime.min.replace(tzinfo=timezone.utc)

    def order(a: Article):
        return (-severity.severity(f"{a.title} {a.summary}")[0], -a.tier.rank, -(a.published or epoch).timestamp())

    on = sorted((a for a in fresh if on_mission(a, cfg) or islamophobia.is_islamophobia(f"{a.title} {a.summary}")), key=order)
    off = sorted((a for a in fresh if a not in on), key=order)
    reserved = []
    for name, key in (("palestine", "palestine_min_per_run"), ("islamophobia", "islamophobia_min_per_run")):
        reserved += [a for a in on if pillar(a, cfg) == name][: cfg.get("pillars", {}).get(key, 1)]
    picked, counts = [], {}
    for a in reserved + [a for a in on if a not in reserved] + off[:off_max]:
        if len(picked) >= cap:
            break
        if a in picked or counts.get(a.source_name, 0) >= per_source:
            continue
        counts[a.source_name] = counts.get(a.source_name, 0) + 1
        picked.append(a)
    log.info("selected %d of %d new: %d on-mission, %d off-mission", len(picked), len(fresh),
             sum(a in on for a in picked), sum(a not in on for a in picked))
    return picked


def handle_article(store, article: Article, ids: dict, ai_call, cfg: dict, stats: dict, recent: list[str],
                   watch: list[dict], pool: list[Article], *, fast: bool = False, level: int = 0) -> bool:
    """Everything that happens to ONE story: claim, celebrity rules, draft, duplicate check, save, post.
    Used by the 4-hour run and by the fast track. Returns False when the AI is unavailable (caller stops)."""
    article_id = ids.get(article.url)
    if not claim_article(store, article_id):
        stats["already_taken"] = stats.get("already_taken", 0) + 1
        return True

    category, tags, flags, extra, name, kind = None, [], [], "", None, ""
    if cfg.get("celebrity", {}).get("enabled") and watch:
        name = celeb_mod.match(article, watch)
    if name:
        lvl = celeb_mod.event_level(f"{article.title} {article.summary}")
        if lvl < 2:
            stats["celebrity_skipped"] = stats.get("celebrity_skipped", 0) + 1
            return True
        kind = celeb_mod.lock(store, name, lvl, cfg)
        if kind is None:
            stats["celebrity_repeat"] = stats.get("celebrity_repeat", 0) + 1  # already covered today
            return True
        others = [o for o in pool if o is not article and celeb_mod.match(o, watch) == name]
        for o in others:
            claim_article(store, ids.get(o.url))  # their info goes into THIS post; they must not post again
        earlier = celeb_mod.earlier_headline(store, name, cfg) if kind == "update" else ""
        article = celeb_mod.combine(article, others, name, kind, earlier)
        category, tags, extra = "celebrity", [celeb_mod.hashtag(name)], celeb_mod.RULES
        flags = ["update"] if kind == "update" else []
    elif fast and level >= 3:
        category = "breaking"

    def give_back():
        if name:
            celeb_mod.release(store, name, kind, cfg)

    try:
        outcome = pipeline.process_article(article, ai_call, cfg, category=category, extra_rules=extra,
                                           tags=tags, flags=flags)
    except ai.AIUnavailable as exc:
        stats["ai_error"] = str(exc)[:200]
        log.error("AI unavailable, stopping drafting: %s", stats["ai_error"])
        release_article(store, article_id)
        give_back()
        return False
    except Exception as exc:  # noqa: BLE001 - one bad article must not stop the run
        log.exception("article failed: %s (%s)", article.url, type(exc).__name__)
        give_back()
        return True
    log.info("%s -> %s %s", article.url, outcome.action, outcome.reason)
    mark_processed(store, article_id)
    if outcome.draft is not None:
        twin = duplicates.find_duplicate(outcome.draft.headline, recent)
        if twin:
            stats["duplicate"] = stats.get("duplicate", 0) + 1
            log.info("duplicate story skipped: %r is like %r", outcome.draft.headline[:70], twin[:70])
            give_back()
            return True
        recent.append(outcome.draft.headline)
    stats[outcome.action] = stats.get(outcome.action, 0) + 1
    if outcome.draft is None:
        give_back()
        return True
    draft_id = save_draft(store, article_id, outcome)
    if name:
        celeb_mod.remember(store, name, outcome.draft.headline, cfg)
    if outcome.action == "needs_approval" and stats.get("previews_sent", 0) < cfg.get("run", {}).get("max_telegram_drafts", 5):
        d = outcome.draft
        owner_feed.draft_preview(d.headline, d.body, d.lane.value, outcome.reason, d.source_urls, draft_id)
        stats["previews_sent"] = stats.get("previews_sent", 0) + 1
    elif outcome.action == "ready":
        publish_ready(store, draft_id, outcome.draft, cfg, stats, fast=fast)
    return True


def fetch_all(priority: bool) -> list[Article]:
    found: list[Article] = []
    for src in sources.feeds(only_priority=priority):
        try:
            items = ingest.fetch_source(src)
        except Exception as exc:  # noqa: BLE001 - one dead feed must not stop the run
            log.warning("feed failed: %s (%s)", src.name, type(exc).__name__)
            continue
        log.info("feed ok: %s (tier %s): %d items", src.name, src.tier.value, len(items))
        found.extend(items)
    return found


def run(priority: bool = False, *, store=None, ai_call: Callable[[str], str] | None = None,
        cfg: dict | None = None, fetcher: Callable[[bool], list[Article]] = fetch_all) -> dict:
    cfg = cfg or load_config()
    limits = cfg.get("run", {})
    ai_call = ai_call or ai.generate
    stats = {"fetched": 0, "new": 0, "stored": 0, "skipped": 0, "held": 0, "needs_approval": 0,
             "ready": 0, "previews_sent": 0, "ai_error": ""}

    fetched = fetcher(priority)
    stats["fetched"] = len(fetched)
    fresh = ingest.dedupe(ingest.within_window(fetched, cfg=cfg), known_keys(store, limits.get("dedupe_days", 7)))
    stats["new"] = len(fresh)
    log.info("%d fetched, %d new after dedupe and window", len(fetched), len(fresh))

    try:
        stats["cleaned"] = sum(cleanup.run(store, cfg).values())
        from . import socialtoken
        for line in socialtoken.refresh_due(store):
            log.info(line)
            if "failed" in line or "error" in line:
                owner_feed.error("token refresh", line, cfg)
    except Exception as exc:  # noqa: BLE001 - housekeeping must never stop a run
        log.warning("cleanup skipped (%s)", type(exc).__name__)
    try:
        from . import insights
        stats["insights"] = insights.collect(store, cfg)["read"]
        insights.weekly_report(store, cfg)
    except Exception as exc:  # noqa: BLE001 - measuring reach must never stop posting
        log.warning("insights skipped (%s)", type(exc).__name__)
    retry_unposted(store, cfg, stats)
    recent = duplicates.recent_headlines(store, cfg.get("retention", {}).get("hours", 72))

    ids: dict[str, int | None] = {}
    for article in fresh:  # store every new article, even those we do not draft this run
        ids[article.url] = save_article(store, article)
        if ids[article.url]:
            stats["stored"] += 1

    watch = celeb_mod.watchlist(cfg, store)
    for article in select_for_run(fresh, cfg):
        if not handle_article(store, article, ids, ai_call, cfg, stats, recent, watch, fresh):
            break
    return stats
