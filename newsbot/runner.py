"""End-to-end run: fetch -> dedupe -> store -> claims -> verify -> draft -> store -> post or review.

Everything is saved to Supabase and logged in detail. In auto mode Green and Amber drafts are posted
(with a branded card); Red drafts, and everything in review mode, go to the owner on Telegram with buttons.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import ai, cleanup, duplicates, ingest, owner_feed, pipeline, scoring, sources
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
                                "label": d.label, "flags": d.flags})
        return row.get("id")
    except Exception as exc:  # noqa: BLE001
        log.warning("draft/claims not saved (%s)", type(exc).__name__)
        return None


def publish_ready(store, draft_id, draft, cfg: dict, stats: dict, final: bool = False) -> None:
    """Auto mode: post a Green/Amber draft live, record it, respect the daily cap. Needs the database."""
    from . import approvals

    if store is None or draft_id is None:
        stats["publish_blocked"] = stats.get("publish_blocked", 0) + 1
        owner_feed.error("auto-post", "draft not posted: it could not be saved, so the daily cap cannot be checked", cfg)
        return
    try:
        if approvals._posted_today(store) >= cfg["limits"]["MAX_POSTS_PER_DAY"]:
            stats["over_daily_limit"] = stats.get("over_daily_limit", 0) + 1
            return
        now = datetime.now(timezone.utc).isoformat()
        if not store.claim("drafts", draft_id, "pending", "approved", {"decided_at": now}):
            stats["already_claimed"] = stats.get("already_claimed", 0) + 1  # another job is posting it: never twice
            return
        results = pipeline.publish_draft(draft, "", dry_run=False, cfg=cfg)
        done = [r for r in results if r.ok and not r.skipped]
        for r in done:
            store.insert("posts", {"draft_id": draft_id, "platform": r.platform, "platform_post_id": r.post_id})
        store.update("drafts", {"id": draft_id}, {"status": "posted" if done else ("skipped" if final else "pending")})
        stats["published"] = stats.get("published", 0) + (1 if done else 0)
        if not done:
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
            if store.select_raw("posts", {"draft_id": f"eq.{row['id']}", "select": "id"}):
                continue
            draft = Draft(headline=row["headline"], body=row["body"], lane=Lane(row["lane"]),
                          source_urls=row.get("sources") or [], label=row.get("label") or "", flags=row.get("flags") or [])
            publish_ready(store, row["id"], draft, cfg, stats, final=True)
            stats["retried"] = stats.get("retried", 0) + 1
    except Exception as exc:  # noqa: BLE001
        log.warning("retry of unposted drafts skipped (%s)", type(exc).__name__)


def on_mission(a: Article, cfg: dict) -> bool:
    text = f"{a.title} {a.summary}".lower()
    return scoring.has_watch_topic(text, cfg) or any(k in text for k in cfg.get("mission_keywords", []))


def select_for_run(fresh: list[Article], cfg: dict) -> list[Article]:
    """Choose what to draft: mission topics first (best tier, newest), few per source, a small off-mission quota."""
    limits = cfg.get("run", {})
    cap, per_source = limits.get("max_articles", 8), limits.get("max_per_source", 3)
    off_max = limits.get("off_mission_max", 2)
    epoch = datetime.min.replace(tzinfo=timezone.utc)

    def order(a: Article):
        return (-a.tier.rank, -(a.published or epoch).timestamp())

    on = sorted((a for a in fresh if on_mission(a, cfg)), key=order)
    off = sorted((a for a in fresh if not on_mission(a, cfg)), key=order)
    picked, counts = [], {}
    for a in on + off[:off_max]:
        if len(picked) >= cap:
            break
        if counts.get(a.source_name, 0) >= per_source:
            continue
        counts[a.source_name] = counts.get(a.source_name, 0) + 1
        picked.append(a)
    log.info("selected %d of %d new: %d on-mission, %d off-mission", len(picked), len(fresh),
             sum(on_mission(a, cfg) for a in picked), sum(not on_mission(a, cfg) for a in picked))
    return picked


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
    except Exception as exc:  # noqa: BLE001 - housekeeping must never stop a run
        log.warning("cleanup skipped (%s)", type(exc).__name__)
    retry_unposted(store, cfg, stats)
    recent = duplicates.recent_headlines(store, cfg.get("retention", {}).get("hours", 72))

    ids: dict[str, int | None] = {}
    for article in fresh:  # store every new article, even those we do not draft this run
        ids[article.url] = save_article(store, article)
        if ids[article.url]:
            stats["stored"] += 1

    for article in select_for_run(fresh, cfg):
        try:
            outcome = pipeline.process_article(article, ai_call, cfg)
        except ai.AIUnavailable as exc:
            stats["ai_error"] = str(exc)[:200]
            log.error("AI unavailable, stopping drafting: %s", stats["ai_error"])
            break
        except Exception as exc:  # noqa: BLE001 - one bad article must not stop the run
            log.exception("article failed: %s (%s)", article.url, type(exc).__name__)
            mark_processed(store, ids.get(article.url))  # do not let a poison article block every future run
            continue
        log.info("%s -> %s %s", article.url, outcome.action, outcome.reason)
        mark_processed(store, ids.get(article.url))
        if outcome.draft is not None:
            twin = duplicates.find_duplicate(outcome.draft.headline, recent)
            if twin:
                stats["duplicate"] = stats.get("duplicate", 0) + 1
                log.info("duplicate story skipped: %r is like %r", outcome.draft.headline[:70], twin[:70])
                continue
            recent.append(outcome.draft.headline)
        stats[outcome.action] = stats.get(outcome.action, 0) + 1
        if outcome.draft is None:
            continue
        draft_id = save_draft(store, ids.get(article.url), outcome)
        if outcome.action == "needs_approval" and stats["previews_sent"] < limits.get("max_telegram_drafts", 5):
            d = outcome.draft
            owner_feed.draft_preview(d.headline, d.body, d.lane.value, outcome.reason, d.source_urls, draft_id)
            stats["previews_sent"] += 1
        elif outcome.action == "ready":
            publish_ready(store, draft_id, outcome.draft, cfg, stats)
    return stats
