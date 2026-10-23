"""Approve or reject drafts from Telegram buttons. Replaces the Cloudflare Worker.

A scheduled job polls Telegram for button taps (callback queries) from the owner only. Approve publishes the
draft live to every enabled platform; Reject closes it. Each draft can be acted on once, expires after
`approvals.max_age_hours`, and counts against MAX_POSTS_PER_DAY.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import owner_feed, pipeline
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

    store.update("drafts", {"id": raw_id}, {"status": "approved", "decided_at": now.isoformat()})  # claim it first
    _close_buttons(cb)
    draft = Draft(headline=row["headline"], body=row["body"], lane=Lane(row["lane"]),
                  source_urls=row.get("sources") or [], label=row.get("label") or "", flags=row.get("flags") or [])
    results = pipeline.publish_draft(draft, "", dry_run=False, alert=owner_feed.message, cfg=cfg)
    done = [r for r in results if r.ok and not r.skipped]
    for r in done:
        store.insert("posts", {"draft_id": int(raw_id), "platform": r.platform, "platform_post_id": r.post_id})
    if done:
        store.update("drafts", {"id": raw_id}, {"status": "posted"})
        _answer(cb["id"], "Posted to " + ", ".join(r.platform for r in done))
        return f"draft {raw_id} posted: " + ", ".join(r.platform for r in done)
    store.update("drafts", {"id": raw_id}, {"status": "pending"})  # nothing went out: allow another try
    errors = "; ".join(f"{r.platform}: {r.error}" for r in results) or "no platform enabled"
    _answer(cb["id"], "Not posted: " + errors)
    owner_feed.error("approve draft " + raw_id, errors, cfg)
    return f"draft {raw_id} NOT posted: {errors}"


def poll(store, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    stats = {"updates": 0, "handled": 0, "errors": 0, "results": []}
    try:
        _call("deleteWebhook", {"drop_pending_updates": "false"})  # getUpdates fails while a webhook is set
    except Exception:  # noqa: BLE001
        log.warning("deleteWebhook failed")
    rows = store.select_raw("settings", {"key": "eq.telegram_offset"})
    offset = int(rows[0]["value"]) if rows else 0
    updates = _call("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": '["callback_query"]'})
    stats["updates"] = len(updates)
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        cb = u.get("callback_query")
        if not cb:
            continue
        try:
            stats["results"].append(handle(store, cb, cfg))
            stats["handled"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad tap must not block the rest
            stats["errors"] += 1
            log.exception("callback failed (%s)", type(exc).__name__)
            _answer(cb["id"], "Error. Check the log.")
    store.upsert("settings", {"key": "telegram_offset", "value": str(offset)}, "key")
    return stats
