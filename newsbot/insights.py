"""Reach measurement (plan section 2 row 14 and section 18): what did people engage with, when, and where?

collect(): for posts a few hours old, read engagement from each platform and keep one small record per post in the
settings table (key stat:<YYYYMMDD>:<platform>:<id>, kept 60 days; the news data itself is deleted after 72 hours).
weekly_report(): every Monday morning IST, a Telegram summary with best hours, topics, platforms and top posts.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import owner_feed, template
from .config import load_config
from .models import Draft
from .publish import instagram, meta_fb, threads
from .publish.base import is_rate_limit

log = logging.getLogger("haqq.insights")
IST = ZoneInfo("Asia/Kolkata")
FETCH = {"facebook": meta_fb.metrics, "threads": threads.metrics, "instagram": instagram.metrics}


def score(platform: str, m: dict) -> int:
    """Engagement points: a share or save counts more than a like."""
    if platform == "facebook":
        return m.get("reactions", 0) + 2 * m.get("comments", 0) + 3 * m.get("shares", 0)
    if platform == "threads":
        return m.get("likes", 0) + 2 * m.get("replies", 0) + 3 * (m.get("reposts", 0) + m.get("quotes", 0))
    return m.get("likes", 0) + 2 * m.get("comments", 0) + 3 * (m.get("saved", 0) + m.get("shares", 0))


def collect(store, cfg: dict | None = None, now: datetime | None = None) -> dict:
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    ic = cfg.get("insights", {})
    out = {"read": 0, "failed": 0}
    if store is None or not ic.get("enabled", True):
        return out
    rows = store.select_raw("posts", {
        "select": "platform,platform_post_id,posted_at,drafts(headline,lane,flags)",
        "posted_at": f"gte.{(now - timedelta(hours=72)).isoformat()}", "limit": "300"})
    cutoff = now - timedelta(hours=ic.get("collect_min_hours", 3))
    budget = ic.get("max_calls_per_run", 20)  # Instagram gives small accounts a small call allowance: few reads, each post at most twice
    throttled: set[str] = set()
    for r in rows:
        if budget <= 0:
            break
        posted = datetime.fromisoformat(r["posted_at"].replace("Z", "+00:00"))
        fetch = FETCH.get(r["platform"])
        if posted > cutoff or not fetch or not r.get("platform_post_id"):
            continue
        d = r.get("drafts") or {}
        skey = f"stat:{posted:%Y%m%d}:{r['platform']}:{r['platform_post_id']}"
        if r["platform"] in throttled:
            continue
        try:
            old = store.get_setting(skey, "")
            if old:  # a second look at 24h+ only if the first was taken early; otherwise one read per post is enough
                first_read = json.loads(old).get("r", 99)
                if first_read >= 20 or (now - posted).total_seconds() < 24 * 3600:
                    continue
        except Exception:  # noqa: BLE001
            pass
        budget -= 1
        try:
            m = fetch(r["platform_post_id"])
        except Exception as exc:  # noqa: BLE001 - a missing insights permission must not stop the run
            out["failed"] += 1
            if is_rate_limit(str(exc)):
                throttled.add(r["platform"])
            log.warning("insights unavailable for %s (%s)", r["platform"], type(exc).__name__)
            continue
        draft = Draft.from_row({"headline": d.get("headline", ""), "body": "", "lane": d.get("lane", "amber"), "flags": d.get("flags")})
        rec = {"t": r["posted_at"], "p": r["platform"], "h": d.get("headline", "")[:90], "c": draft.category,
               "tp": template.badge(draft, cfg).title(), "m": m, "s": score(r["platform"], m),
               "r": round((now - posted).total_seconds() / 3600)}
        store.set_setting(f"stat:{posted:%Y%m%d}:{r['platform']}:{r['platform_post_id']}", json.dumps(rec))
        out["read"] += 1
    return out


def _load(store, now: datetime, days: int = 7) -> list[dict]:
    lo = f"stat:{(now - timedelta(days=days)):%Y%m%d}"
    rows = store.select_raw("settings", {"and": f"(key.gte.{lo},key.lt.stat;)", "limit": "2000"})
    return [json.loads(r["value"]) for r in rows]


def _avg(vals: list[int]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def build_report(stats: list[dict], now: datetime) -> str:
    if not stats:
        return "📊 WEEKLY REPORT\nNo engagement data yet. Posts need a few hours before their numbers can be read."
    by_platform, by_bucket, by_topic = defaultdict(list), defaultdict(list), defaultdict(list)
    for s in stats:
        hour = datetime.fromisoformat(s["t"].replace("Z", "+00:00")).astimezone(IST).hour
        by_platform[s["p"]].append(s["s"])
        by_bucket[hour // 3 * 3].append(s["s"])
        by_topic[s["tp"]].append(s["s"])
    posts = len({s["h"] for s in stats})
    lines = [f"📊 WEEKLY REPORT · {(now - timedelta(days=7)):%d %b} to {now:%d %b}", f"Posts measured: {posts}", "",
             "Average engagement per post, by platform:"]
    lines += [f"  {p.title()}: {_avg(v):.1f} ({len(v)} posts)" for p, v in sorted(by_platform.items(), key=lambda kv: -_avg(kv[1]))]
    good = sorted(((b, v) for b, v in by_bucket.items() if len(v) >= 3), key=lambda kv: -_avg(kv[1]))
    if good:
        lines += ["", "Best posting hours (IST):"]
        lines += [f"  {b:02d}:00 to {b + 3:02d}:00: {_avg(v):.1f} ({len(v)} posts)" for b, v in good[:3]]
    topics = sorted(((t, v) for t, v in by_topic.items() if len(v) >= 2), key=lambda kv: -_avg(kv[1]))
    if topics:
        lines += ["", "Best topics:"] + [f"  {t}: {_avg(v):.1f} ({len(v)})" for t, v in topics[:4]]
    top = sorted(stats, key=lambda s: -s["s"])[:3]
    lines += ["", "Top posts:"] + [f"  {i}. {s['h']} ({s['p'].title()}, {s['s']})" for i, s in enumerate(top, 1)]
    if good:
        lines += ["", f"Suggestion: schedule more posts around {good[0][0]:02d}:00 to {good[0][0] + 3:02d}:00 IST."]
    return "\n".join(lines)


def weekly_report(store, cfg: dict | None = None, now: datetime | None = None, force: bool = False) -> bool:
    """Send the report once per ISO week, on the configured weekday after the configured hour (IST)."""
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    ic = cfg.get("insights", {})
    local = now.astimezone(IST)
    if store is None or not ic.get("enabled", True):
        return False
    if not force and (local.weekday() != ic.get("weekly_report_weekday", 0) or local.hour < ic.get("weekly_report_hour_ist", 9)):
        return False
    week = f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"
    if not force and store.get_setting("weekly_report_sent", "") == week:
        return False
    owner_feed.message(build_report(_load(store, now), now))
    store.set_setting("weekly_report_sent", week)
    return True
