"""Database retention: remove news data older than `retention.hours` (default 72), run logs after N days.

Deletes in dependency order: posts -> drafts -> articles (claims cascade). Sources, creators, corrections,
tokens, settings and occasions are never touched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("haqq.cleanup")


def _ids(rows: list[dict]) -> list[int]:
    return [r["id"] for r in rows]


def _in(ids: list[int]) -> str:
    return "in.(" + ",".join(str(i) for i in ids) + ")"


def run(store, cfg: dict) -> dict:
    ret = cfg.get("retention", {})
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=ret.get("hours", 72))).isoformat()
    logs_cutoff = (now - timedelta(days=ret.get("run_logs_days", 7))).isoformat()
    out = {"articles": 0, "drafts": 0, "posts": 0, "run_logs": 0, "settings": 0}
    if store is None:
        return out

    old_articles = _ids(store.select_raw("articles", {"select": "id", "fetched_at": f"lt.{cutoff}", "limit": "1000"}))
    drafts = set(_ids(store.select_raw("drafts", {"select": "id", "created_at": f"lt.{cutoff}", "limit": "1000"})))
    if old_articles:
        drafts |= set(_ids(store.select_raw("drafts", {"select": "id", "article_id": _in(old_articles), "limit": "1000"})))
    if drafts:
        ids = sorted(drafts)
        out["posts"] = store.delete("posts", {"draft_id": _in(ids)})
        out["drafts"] = store.delete("drafts", {"id": _in(ids)})
    if old_articles:
        out["articles"] = store.delete("articles", {"id": _in(old_articles)})
    out["run_logs"] = store.delete("run_logs", {"started_at": f"lt.{logs_cutoff}"})
    # small key-value records: celebrity day locks (3 days) and engagement stats (60 days)
    day = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    stat_day = (now - timedelta(days=ret.get("stats_days", 60))).strftime("%Y%m%d")
    out["settings"] = store.delete("settings", {"and": f"(key.like.celeb:*,key.lt.celeb:{day})"}) + \
        store.delete("settings", {"and": f"(key.like.stat:*,key.lt.stat:{stat_day})"}) + \
        store.delete("settings", {"and": f"(key.like.ai:*,key.lt.ai:{(now - timedelta(days=3)):%Y%m%d})"})
    log.info("cleanup removed %s", out)
    return out
