"""One short morning message (IST) with yesterday's numbers, instead of having to scroll through every post message."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import owner_feed
from .config import load_config

IST = timezone(timedelta(hours=5, minutes=30))


def build(store, now: datetime) -> str:
    since = (now - timedelta(hours=24)).isoformat()
    posts = store.select_raw("posts", {"select": "platform,draft_id,drafts(headline,flags)", "posted_at": f"gte.{since}", "limit": "500"})
    runs = store.select_raw("run_logs", {"select": "name,status", "started_at": f"gte.{since}", "limit": "200"})
    pending = store.select_raw("drafts", {"select": "id", "status": "eq.pending", "limit": "200"})
    by: dict[str, int] = {}
    stories, stories_n = set(), 0
    for p in posts:
        by[p["platform"]] = by.get(p["platform"], 0) + 1
        if p["platform"].endswith("_story"):
            stories_n += 1
        else:
            stories.add(p["draft_id"])
    feed = {k: v for k, v in by.items() if not k.endswith("_story")}
    heads = []
    for p in posts:
        h = (p.get("drafts") or {}).get("headline", "")
        if h and h not in heads and not p["platform"].endswith("_story"):
            heads.append(h)
    bad = [r for r in runs if r["status"] != "ok"]
    full = [r for r in runs if r["name"].startswith("run")]
    lines = [f"☀️ DAILY DIGEST · {now.astimezone(IST):%d %b}", "",
             f"Stories published: {len(stories)}  ·  by platform: " + (", ".join(f"{k.title()} {v}" for k, v in sorted(feed.items())) or "none"),
             f"Facebook and Instagram stories: {stories_n}",
             f"Full runs: {len(full)} of 6 expected" + (f"  ·  ⚠️ {len(bad)} run(s) with errors" if bad else ""),
             f"Waiting in queue: {len(pending)}"]
    if len(feed) < 3 and feed:
        lines.append("⚠️ Not every platform posted: check the POSTED messages for the error.")
    if not posts:
        lines.append("⚠️ Nothing was posted in 24 hours: send /status and /tokenstatus.")
    if heads:
        lines += ["", "Posted:"] + [f"• {h[:90]}" for h in heads[:6]]
    return "\n".join(lines)


def send_daily(store, cfg: dict | None = None, now: datetime | None = None, force: bool = False) -> bool:
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    dc = cfg.get("digest", {})
    if store is None or not dc.get("enabled", True):
        return False
    local = now.astimezone(IST)
    if not force and local.hour < dc.get("hour_ist", 8):
        return False
    day = f"{local:%Y%m%d}"
    if not force and store.get_setting("digest_sent", "") == day:
        return False
    store.set_setting("digest_sent", day)  # first: a failure below must not repeat the message every run
    owner_feed.message(build(store, now))
    return True
