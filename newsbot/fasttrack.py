"""Fast track: critical and very high priority news, plus celebrity events, checked every few minutes.

Runs inside the always-on listener (GitHub's cron cannot give real time). Per cycle:
  1. fetch only the fast sources (trusted Tier A/B outlets and news-search feeds);
  2. keep stories that are fresh (under `max_age_minutes`), on mission and severe enough (severity.py),
     plus qualifying events about watched celebrities;
  3. rank (severity, tier, newest) and hand the best few to runner.handle_article, which claims the article
     in the database, applies the celebrity once-a-day rule, drafts, checks duplicates and posts.
One trusted source is enough here: Tier A/B claims pass verification alone (verify.evaluate_claim).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import ai, duplicates, ingest, runner, severity, sources
from . import celebrity as celeb_mod
from .config import load_config
from .models import Article, Tier
from .sources import Source

log = logging.getLogger("haqq.fast")


def fetch_fast() -> list[Article]:
    found: list[Article] = []
    for src in sources.feeds(only_fast=True):
        try:
            items = ingest.fetch_source(src)
        except Exception as exc:  # noqa: BLE001 - a dead feed must not stop the cycle
            log.warning("fast feed failed: %s (%s)", src.name, type(exc).__name__)
            continue
        found.extend(items)
    return found


def celebrity_sources(watch: list[dict], chunk: int = 6) -> list[Source]:
    """Google News searches for the watched people, 6 names per query."""
    names = [p["name"] for p in watch]
    out = []
    for i in range(0, len(names), chunk):
        q = "+OR+".join('%22' + n.replace(" ", "+") + '%22' for n in names[i:i + chunk])
        out.append(Source(name=f"Google News: people {i // chunk + 1}", tier=Tier.B, aggregator=True, fast=True,
                          feed=f"https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q={q}+when:1d"))
    return out


def fetch_celebrities(watch: list[dict]) -> list[Article]:
    found: list[Article] = []
    for src in celebrity_sources(watch):
        try:
            found.extend(ingest.fetch_source(src))
        except Exception as exc:  # noqa: BLE001
            log.warning("celebrity feed failed (%s)", type(exc).__name__)
    return found


def classify(a: Article, cfg: dict, watch: list[dict], now: datetime) -> tuple[str, int] | None:
    """("celebrity"|"breaking", level) when this story belongs on the fast track, else None."""
    if a.published is None:
        return None
    age = now - a.published
    text = f"{a.title} {a.summary}"
    ft, cel = cfg.get("fast_track", {}), cfg.get("celebrity", {})
    if cel.get("enabled") and watch and celeb_mod.match(a, watch):
        lvl = celeb_mod.event_level(text)
        if lvl >= 2 and age <= timedelta(hours=cel.get("max_age_hours", 24)):
            return "celebrity", lvl
        return None
    if age > timedelta(minutes=ft.get("max_age_minutes", 180)):
        return None
    from .islamophobia import is_islamophobia
    if not (runner.on_mission(a, cfg) or is_islamophobia(text)):
        return None
    lvl, _ = severity.severity(text)
    return ("breaking", lvl) if lvl >= ft.get("min_severity", 2) else None


def cycle(store, cfg: dict | None = None, ai_call=None, state: dict | None = None, *, fetcher=fetch_fast,
          celeb_fetcher=fetch_celebrities, now: datetime | None = None) -> dict:
    cfg = cfg or load_config()
    ft = cfg.get("fast_track", {})
    state = state if state is not None else {}
    now = now or datetime.now(timezone.utc)
    stats: dict = {"fast_candidates": 0, "published": 0}
    if not ft.get("enabled", True) or store is None or runner.is_paused(store):
        return stats

    try:
        if store.get_setting("fast_track", "on") == "off":  # /fast off in Telegram
            return stats
    except Exception:  # noqa: BLE001
        pass

    watch = celeb_mod.watchlist(cfg, store)
    articles = fetcher()
    if watch and cfg.get("celebrity", {}).get("enabled") and now >= state.get("next_celeb", now):
        articles += celeb_fetcher(watch)
        state["next_celeb"] = now + timedelta(minutes=cfg.get("celebrity", {}).get("poll_minutes", 30))

    known = runner.known_keys(store, 3)
    pool = [a for a in ingest.dedupe(articles, known) if classify(a, cfg, watch, now)]
    if not pool:
        return stats
    kinds = {a.url: classify(a, cfg, watch, now) for a in pool}
    stats["fast_candidates"] = len(pool)

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    ranked = sorted(pool, key=lambda a: (-kinds[a.url][1], -a.tier.rank, -(a.published or epoch).timestamp()))
    ids = {a.url: runner.save_article(store, a) for a in pool}
    recent = duplicates.recent_headlines(store, cfg.get("retention", {}).get("hours", 72))
    budget = max(0, ft.get("max_per_day", 6) - runner.fast_posted_today(store))
    per_cycle = ft.get("max_per_cycle", 2)
    handled = 0
    for a in ranked:
        kind, level = kinds[a.url]
        if handled >= per_cycle:
            break
        if kind == "breaking":
            if budget <= 0:
                stats["fast_over_budget"] = stats.get("fast_over_budget", 0) + 1
                continue
            if duplicates.find_duplicate(a.title, recent):  # same story already covered: close it quietly
                runner.claim_article(store, ids.get(a.url))
                stats["duplicate"] = stats.get("duplicate", 0) + 1
                continue
        before = stats["published"]
        if not runner.handle_article(store, a, ids, ai_call or ai.generate, cfg, stats, recent, watch, pool,
                                     fast=True, level=level):
            break
        handled += 1
        if kind == "breaking" and stats["published"] > before:
            budget -= 1
    return stats
