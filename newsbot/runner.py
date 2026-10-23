"""End-to-end run: fetch -> dedupe -> store -> claims -> verify -> draft -> store -> review/dry-run.

Everything is saved to Supabase when it is reachable and logged in detail. NOTHING is published:
drafts go to the owner on Telegram for review, and `ready` drafts are only dry-run published.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import ai, ingest, owner_feed, pipeline, sources
from .config import load_config
from .models import Article

log = logging.getLogger("haqq.run")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def known_keys(store, days: int) -> set[str]:
    """URLs already stored (normalised like Article.key), so a re-run does not redo old stories."""
    if store is None:
        return set()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = store.select_raw("articles", {"select": "url", "fetched_at": f"gte.{since}"})
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read stored articles (%s); dedupe uses this run only", type(exc).__name__)
        return set()
    return {r["url"].strip().lower().rstrip("/") for r in rows}


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


def save_draft(store, article_id: int | None, outcome: pipeline.Outcome) -> None:
    d = outcome.draft
    if store is None or d is None or article_id is None:
        return
    try:
        for c in d.claims:
            store.insert("claims", {"article_id": article_id, "text": c.text, "attribution": c.attribution,
                                    "status": c.status, "verified_by": c.verified_by})
        store.insert("drafts", {"article_id": article_id, "lane": d.lane.value, "status": "pending",
                                "headline": d.headline, "body": d.body, "sources": d.source_urls,
                                "label": d.label, "flags": d.flags})
    except Exception as exc:  # noqa: BLE001
        log.warning("draft/claims not saved (%s)", type(exc).__name__)


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

    for article in fresh:  # store every new article, even those we do not draft this run
        if save_article(store, article):
            stats["stored"] += 1

    for article in fresh[: limits.get("max_articles", 8)]:
        try:
            outcome = pipeline.process_article(article, ai_call, cfg)
        except ai.AIUnavailable as exc:
            stats["ai_error"] = str(exc)[:200]
            log.error("AI unavailable, stopping drafting: %s", stats["ai_error"])
            break
        except Exception as exc:  # noqa: BLE001 - one bad article must not stop the run
            log.exception("article failed: %s (%s)", article.url, type(exc).__name__)
            continue
        log.info("%s -> %s %s", article.url, outcome.action, outcome.reason)
        stats[outcome.action] = stats.get(outcome.action, 0) + 1
        if outcome.draft is None:
            continue
        save_draft(store, save_article(store, article), outcome)
        if outcome.action == "needs_approval" and stats["previews_sent"] < limits.get("max_telegram_drafts", 5):
            d = outcome.draft
            owner_feed.draft_preview(d.headline, d.body, d.lane.value, outcome.reason, d.source_urls)
            stats["previews_sent"] += 1
        elif outcome.action == "ready":
            pipeline.publish_draft(outcome.draft, "", dry_run=True, cfg=cfg)
    return stats
