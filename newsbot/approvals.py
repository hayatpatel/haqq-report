"""Approve or reject drafts from Telegram buttons. Replaces the Cloudflare Worker.

A scheduled job polls Telegram for button taps (callback queries) from the owner only. Approve publishes the
draft live to every enabled platform; Reject closes it. Each draft can be acted on once, expires after
`approvals.max_age_hours`, and counts against MAX_POSTS_PER_DAY.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from . import fbtoken, owner_feed, pipeline, socialtoken
from .config import load_config, optional_env
from .models import Draft, Lane
from .publish.telegram_channel import _call

log = logging.getLogger("haqq.approvals")


def _answer(cb_id: str, text: str) -> None:
    try:
        _call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:190]})
    except Exception:  # noqa: BLE001
        log.warning("callback answer not sent")


def _close_buttons(cb: dict) -> None:
    msg = cb.get("message") or {}
    try:
        _call("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"],
                                         "reply_markup": '{"inline_keyboard":[]}'})
    except Exception:  # noqa: BLE001
        log.warning("buttons not removed")


def _posted_today(store) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return len(store.select_raw("drafts", {"status": "eq.posted", "decided_at": f"gte.{start}", "select": "id"}))


def handle(store, cb: dict, cfg: dict) -> str:
    """Process one button tap. Returns a short result for the run log."""
    data = cb.get("data", "")
    action, _, raw_id = data.partition(":")
    if str(cb.get("from", {}).get("id")) != str(optional_env("TELEGRAM_OWNER_ID")):
        _answer(cb["id"], "Not allowed")
        return "ignored: not the owner"
    if action not in ("a", "r") or not raw_id.isdigit():
        _answer(cb["id"], "Unknown action")
        return f"ignored: {data[:20]}"
    rows = store.select_raw("drafts", {"id": f"eq.{raw_id}"})
    if not rows:
        _answer(cb["id"], "Draft not found")
        return f"draft {raw_id} not found"
    row = rows[0]
    if row["status"] != "pending":
        _answer(cb["id"], f"Already {row['status']}")
        _close_buttons(cb)
        return f"draft {raw_id} already {row['status']}"

    now = datetime.now(timezone.utc)
    if action == "r":
        store.update("drafts", {"id": raw_id}, {"status": "rejected", "decided_at": now.isoformat()})
        _answer(cb["id"], "Rejected")
        _close_buttons(cb)
        return f"draft {raw_id} rejected"

    max_age = cfg.get("approvals", {}).get("max_age_hours", 24)
    created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    if now - created > timedelta(hours=max_age):
        store.update("drafts", {"id": raw_id}, {"status": "skipped", "decided_at": now.isoformat()})
        _answer(cb["id"], f"Too old (over {max_age}h). Not posted.")
        _close_buttons(cb)
        return f"draft {raw_id} expired"
    if _posted_today(store) >= cfg["limits"]["MAX_POSTS_PER_DAY"]:
        _answer(cb["id"], "Daily post limit reached. Try tomorrow.")
        return f"draft {raw_id} blocked by daily limit"

    if not store.claim("drafts", raw_id, "pending", "approved", {"decided_at": now.isoformat()}):
        _answer(cb["id"], "Already being handled")
        return f"draft {raw_id} already claimed"
    _close_buttons(cb)
    draft = Draft(headline=row["headline"], body=row["body"], lane=Lane(row["lane"]),
                  source_urls=row.get("sources") or [], label=row.get("label") or "", flags=row.get("flags") or [])
    results = pipeline.publish_draft(draft, "", dry_run=False, cfg=cfg)
    done = [r for r in results if r.ok and not r.skipped]
    for r in done:
        store.insert("posts", {"draft_id": int(raw_id), "platform": r.platform, "platform_post_id": r.post_id})
    if done:
        store.update("drafts", {"id": raw_id}, {"status": "posted"})
        _answer(cb["id"], "Posted to " + ", ".join(r.platform for r in done))
        return f"draft {raw_id} posted: " + ", ".join(r.platform for r in done)
    store.update("drafts", {"id": raw_id}, {"status": "pending"})  # nothing went out: allow another try
    errors = "; ".join(f"{r.platform}: {r.error}" for r in results) or "no platform enabled"
    _answer(cb["id"], "Not posted. See the message in this chat.")
    return f"draft {raw_id} NOT posted: {errors}"


def status_text(store) -> str:
    from . import runner

    cfg = load_config()
    paused = runner.is_paused(store)
    pending = len(store.select_raw("drafts", {"select": "id", "status": "eq.pending", "lane": "in.(green,amber)"}))
    return "\n".join([
        "⏸️ STATUS · PAUSED" if paused else "🟢 STATUS · running",
        f"Mode: {cfg.get('mode')} · fast track: {store.get_setting('fast_track', 'on')}",
        f"Posted today: {_posted_today(store)} of {cfg['limits']['MAX_POSTS_PER_DAY']} (fast track extra: {cfg.get('fast_track', {}).get('extra_daily_posts', 0)})",
        f"Waiting to post: {pending}",
        "Platforms on: " + ", ".join(k for k, v in cfg["enable"].items() if v),
    ])


def celeb_command(store, rest: str) -> str:
    from . import celebrity

    cfg = load_config()
    watch = celebrity.watchlist(cfg, store)
    action, _, name = rest.strip().partition(" ")
    name = name.strip()
    action = action.lower()
    if action == "add" and name:
        if not any(p["name"].lower() == name.lower() for p in watch):
            watch.append({"name": name})
        store.set_setting("celebrities", json.dumps(watch))
        return f"✅ WATCHLIST · added {name}\nOne post per day for each person, trusted outlets only, no gossip."
    if action == "remove" and name:
        watch = [p for p in watch if p["name"].lower() != name.lower()]
        store.set_setting("celebrities", json.dumps(watch))
        return f"✅ WATCHLIST · removed {name}"
    return "👤 CELEBRITY WATCHLIST (" + str(len(watch)) + ")\n" + ", ".join(p["name"] for p in watch) + \
        "\n\n/celeb add NAME · /celeb remove NAME"


def handle_message(store, msg: dict) -> str:
    """Owner commands typed to the bot: /fbtoken, /tokenstatus, /help."""
    owner = str(optional_env("TELEGRAM_OWNER_ID"))
    if str(msg.get("from", {}).get("id")) != owner or str(msg.get("chat", {}).get("id")) != owner:
        return "ignored: not the owner"
    text = (msg.get("text") or "").strip()
    cmd, _, rest = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/fbtoken":
        reply = fbtoken.update_from_text(rest, store)
        if rest.strip() and rest.strip().lower() != "reset":  # the message holds a secret: remove it
            try:
                _call("deleteMessage", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]})
            except Exception:  # noqa: BLE001
                reply += "\nI could not delete your message. Please delete it yourself."
        owner_feed.message(reply)
        return "fbtoken: " + reply.splitlines()[0]
    if cmd in ("/igtoken", "/thtoken"):
        kind = "instagram" if cmd == "/igtoken" else "threads"
        reply = socialtoken.update_from_text(kind, rest, store)
        if rest.strip() and rest.strip().lower() != "reset":  # the message holds a secret: remove it
            try:
                _call("deleteMessage", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]})
            except Exception:  # noqa: BLE001
                reply += "\nI could not delete your message. Please delete it yourself."
        owner_feed.message(reply)
        return f"{cmd[1:]}: " + reply.splitlines()[0]
    if cmd == "/igsetup":
        owner_feed.message(socialtoken.setup_page_route(rest, store))
        return "igsetup"
    if cmd == "/tokenstatus":
        socialtoken.apply_overrides(store)
        owner_feed.message(fbtoken.status(store) + "\n\n" + socialtoken.status())
        return "tokenstatus sent"
    if cmd in ("/pause", "/resume"):
        store.set_setting("paused", "true" if cmd == "/pause" else "false")
        owner_feed.message("⏸️ PAUSED · nothing will be posted until you send /resume. Drafts wait and are posted later."
                           if cmd == "/pause" else "▶️ RESUMED · posting is on again.")
        return cmd[1:]
    if cmd == "/fast":
        want = rest.strip().lower()
        if want in ("on", "off"):
            store.set_setting("fast_track", want)
        owner_feed.message(f"⚡ FAST TRACK · {store.get_setting('fast_track', 'on').upper()}\nUse /fast on or /fast off.")
        return "fast " + want
    if cmd == "/status":
        owner_feed.message(status_text(store))
        return "status sent"
    if cmd == "/celeb":
        owner_feed.message(celeb_command(store, rest))
        return "celeb command"
    if cmd in ("/help", "/start"):
        owner_feed.message("Commands:\n/status · what is running\n/pause · /resume · stop or start all posting\n"
                           "/fast on|off · the fast track\n/celeb list|add NAME|remove NAME · the celebrity watchlist\n"
                           "/fbtoken · /igtoken · /thtoken · /igsetup · /tokenstatus · the Facebook, Instagram and Threads tokens\n\n" + fbtoken.HELP)
        return "help sent"
    return "ignored: not a command"


def poll(store, cfg: dict | None = None, wait: int = 0, first: bool = True) -> dict:
    """One check of Telegram. wait>0 holds the request open up to that many seconds (instant replies)."""
    cfg = cfg or load_config()
    stats = {"updates": 0, "handled": 0, "errors": 0, "results": []}
    if first:
        try:
            _call("deleteWebhook", {"drop_pending_updates": "false"})  # getUpdates fails while a webhook is set
        except Exception:  # noqa: BLE001
            log.warning("deleteWebhook failed")
    rows = store.select_raw("settings", {"key": "eq.telegram_offset"})
    offset = int(rows[0]["value"]) if rows else 0
    updates = _call("getUpdates", {"offset": offset, "timeout": wait, "allowed_updates": '["callback_query","message"]'},
                    timeout=wait + 15)
    stats["updates"] = len(updates)
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        cb = u.get("callback_query")
        if not cb:
            msg = u.get("message")
            if msg and str(msg.get("text", "")).startswith("/"):
                try:
                    stats["results"].append(handle_message(store, msg))
                    stats["handled"] += 1
                except Exception as exc:  # noqa: BLE001
                    stats["errors"] += 1
                    log.exception("command failed (%s)", type(exc).__name__)
            continue
        try:
            stats["results"].append(handle(store, cb, cfg))
            stats["handled"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad tap must not block the rest
            stats["errors"] += 1
            log.exception("callback failed (%s)", type(exc).__name__)
            _answer(cb["id"], "Error. Check the log.")
    if updates:
        store.upsert("settings", {"key": "telegram_offset", "value": str(offset)}, "key")
    return stats


def catch_up(store, cfg: dict, now=None, dispatch=None) -> bool:
    """GitHub often skips scheduled runs. When the full pipeline has not run for catchup_hours, start it from here.
    True when a run was requested. Never raises."""
    import os
    import subprocess
    from datetime import datetime, timedelta, timezone

    try:
        now = now or datetime.now(timezone.utc)
        hours = cfg.get("run", {}).get("catchup_hours", 4.7)
        rows = store.select_raw("run_logs", {"name": "like.run*", "select": "started_at", "order": "started_at.desc", "limit": "1"})
        last = datetime.fromisoformat(rows[0]["started_at"].replace("Z", "+00:00")) if rows else None
        if last and now - last < timedelta(hours=hours):
            return False
        asked = store.get_setting("catchup_at", "")
        if asked and now - datetime.fromisoformat(asked) < timedelta(minutes=45):
            return False  # already requested; the run is queued or starting
        store.set_setting("catchup_at", now.isoformat())
        (dispatch or (lambda: subprocess.run(["gh", "workflow", "run", "ingest.yml"], check=True, timeout=60,
                                             env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")})))()
        log.info("full pipeline was overdue: requested a run")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("catch-up check failed (%s)", type(exc).__name__)
        return False


def listen(store, minutes: float, *, wait: int = 25, cfg: dict | None = None,
           sleep=None, clock=None) -> dict:
    """Stay on Telegram for `minutes`, answering commands within seconds. Survives temporary errors."""
    import time

    sleep, clock = sleep or time.sleep, clock or time.monotonic
    cfg = cfg or load_config()
    end = clock() + minutes * 60
    totals = {"polls": 0, "handled": 0, "errors": 0, "results": [], "fast_posted": 0}
    first, bad_in_a_row = True, 0
    fast_state: dict = {}
    interval = cfg.get("fast_track", {}).get("interval_seconds", 300)
    next_fast = clock()  # the first fast-track cycle starts right away
    next_catch = clock() + 120
    while clock() < end:
        if clock() >= next_catch:
            next_catch = clock() + 600
            catch_up(store, cfg)
        if cfg.get("fast_track", {}).get("enabled", True) and clock() >= next_fast:
            next_fast = clock() + interval
            try:
                from . import fasttrack
                fs = fasttrack.cycle(store, cfg, state=fast_state)
                totals["fast_posted"] += fs.get("published", 0)
                if fs.get("fast_candidates"):
                    log.info("fast track: %s", {k: v for k, v in fs.items() if v})
            except Exception as exc:  # noqa: BLE001 - never let a bad cycle stop the listener
                totals["errors"] += 1
                log.exception("fast track cycle failed (%s)", type(exc).__name__)
        try:
            stats = poll(store, cfg, wait=wait, first=first)
            first, bad_in_a_row = False, 0
            totals["handled"] += stats["handled"]
            totals["errors"] += stats["errors"]
            totals["results"] += stats["results"]
        except Exception as exc:  # noqa: BLE001 - Telegram or the database hiccuped: wait and carry on
            bad_in_a_row += 1
            totals["errors"] += 1
            log.warning("telegram check failed (%s), attempt %d", type(exc).__name__, bad_in_a_row)
            if bad_in_a_row >= 20:
                raise
            sleep(min(60, 5 * bad_in_a_row))
        totals["polls"] += 1
        if wait == 0:
            sleep(5)
    return totals
