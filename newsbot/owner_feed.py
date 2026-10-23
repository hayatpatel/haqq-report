"""Private Telegram feed to the owner: posts published, errors, daily health report.

Uses TELEGRAM_BOT_TOKEN + TELEGRAM_OWNER_ID only (a private chat with the bot, no public channel).
Every function is a silent no-op when Telegram is not configured and never raises, so the feed can
never break publishing.
"""
from __future__ import annotations

import logging
from typing import Iterable

import os
from pathlib import Path

import requests

from .config import load_config, redact
from .publish.telegram_channel import alert_owner

log = logging.getLogger("haqq.owner_feed")


def _enabled(key: str, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("owner_feed", {}).get(key, True))


def _send(text: str) -> None:
    try:
        alert_owner(text)
    except Exception:  # noqa: BLE001 - the feed must never break the pipeline
        log.warning("owner feed message not sent")


# ---- Standard message templates -------------------------------------------------------------------------
# Every message starts with ONE status line (icon, what happened, context), then short labelled lines.
# Icons: ✅ done  ⚠️ partly done / needs a look  ❌ failed  🔴 needs your decision  🟢 run ok  🧪 test

_FIXES = [
    (("(#200)", '"code": 200'), "The access token lacks a permission.",
     "Regenerate the token with the needed permissions (Facebook: pages_manage_posts, pages_manage_engagement. Instagram: instagram_business_content_publish. Threads: threads_content_publish), then update the secret in GitHub."),
    (('"code": 190', "expired", "Invalid OAuth"), "The access token is expired or invalid.",
     "Create a new token and update the secret in GitHub (Settings, Secrets and variables, Actions)."),
    (("must be at most",), "The caption is longer than the platform allows.", "This is a bug on our side. Send me the log."),
    (("Missing required environment variables",), "A secret is not available to this workflow.",
     "Add the secret in GitHub, or check the workflow passes it under env."),
    (("platform paused",), "Paused after repeated failures in this run.", "Fix the earlier error; it is tried again at the next run."),
    (("needs an image",), "No image was available for this post.", "Check that the card upload to Supabase Storage works (run card-test)."),
    (("rate", '"code": 4,', '"code": 17', '"code": 32', '"code": 613'), "The platform is rate limiting us.", "Nothing to do. It retries next run."),
]


def friendly_error(raw: str) -> tuple[str, str]:
    """(what happened in plain words, what to do). Falls back to the raw text, shortened."""
    for needles, what, fix in _FIXES:
        if any(n in raw for n in needles):
            return what, fix
    return raw.strip()[:200] or "Unknown error.", ""


def _post_link(r) -> str:
    if r.extra.get("url"):
        return r.extra["url"]
    if r.platform == "facebook" and r.post_id:
        return f"https://www.facebook.com/{r.post_id}"
    return f"id {r.post_id}" if r.post_id else "posted"


def post_results(headline: str, results: Iterable, *, dry_run: bool, cfg: dict | None = None,
                 lane: str = "", source: str = "", note: str = "") -> None:
    """One message per story: status line, headline, one line per platform, source."""
    if not _enabled("posts", cfg):
        return
    results = list(results)
    if not results:
        return
    ok = [r for r in results if r.ok and not r.skipped]
    failed = [r for r in results if not r.ok]
    if dry_run:
        status = "🧪 DRY RUN · nothing was posted"
    elif ok and failed:
        status = "⚠️ PARTLY POSTED"
    elif ok:
        status = "✅ POSTED"
    else:
        status = "❌ NOT POSTED"
    if lane:
        status += f" · {lane.upper()}"
    lines = [status, headline[:140], ""]
    fixes = []
    for r in results:
        name = r.platform.replace("_", " ").title()
        if r.skipped and r.ok:
            lines.append(f"{name}: would post")
        elif r.ok:
            lines.append(f"{name}: ✅ {_post_link(r)}")
        else:
            what, fix = friendly_error(r.error or "")
            lines.append(f"{name}: ❌ {what}")
            if fix and fix not in fixes:
                fixes.append(fix)
    if fixes:
        lines += ["", "Fix: " + " ".join(fixes)]
    tail = ([f"Flagged: {note}"] if note and ok else []) + ([f"Source: {source}"] if source else [])
    if tail:
        lines += [""] + tail
    if not dry_run and not ok and not failed:
        return
    _send("\n".join(lines))


def error(where: str, detail: str, cfg: dict | None = None) -> None:
    if _enabled("errors", cfg):
        what, fix = friendly_error(detail)
        lines = [f"❌ ERROR · {where}", f"What: {what}"]
        if fix:
            lines.append(f"Fix: {fix}")
        if what != detail.strip()[:200]:
            lines.append(f"Details: {detail.strip()[:300]}")
        _send("\n".join(lines))


def health_report(report: str, failed: bool, cfg: dict | None = None) -> None:
    """Daily status. Sent every day (not only on failure) when owner_feed.daily_health_report is true."""
    if failed or _enabled("daily_health_report", cfg):
        _send(("🔴 ACTION NEEDED · health check\n\n" if failed else "🟢 HEALTH · daily report\n\n") + report)


def message(text: str) -> None:
    """Free-form notice, used by workflow failure steps."""
    _send(text)


def send_log(path: Path, caption: str = "") -> bool:
    """Send a run log file to the owner's private chat (last ~1 MB). Silent no-op if Telegram is not set up."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_OWNER_ID", "").strip()
    if not token or not chat:
        return False
    try:
        data = Path(path).read_bytes()[-1_000_000:]
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                             data={"chat_id": chat, "caption": redact(caption)[:900]},
                             files={"document": (Path(path).name, data)}, timeout=60)
        return resp.ok
    except Exception:  # noqa: BLE001 - the feed must never break a run
        log.warning("log file not sent")
        return False


_LABELS = {"fetched": "Fetched", "new": "New", "stored": "Stored", "published": "Posted", "needs_approval": "Awaiting you",
           "publish_failed": "Failed", "skipped": "Skipped", "held": "Held", "duplicate": "Duplicates",
           "retried": "Retried", "queued": "Queued for next run", "cleaned": "Cleaned from DB", "over_daily_limit": "Over daily limit",
           "handled": "Taps handled", "errors": "Errors"}


def format_run_summary(name: str, status: str, summary: dict) -> str:
    """Caption for the log file: status line, then short labelled numbers."""
    icon = "🟢" if status == "ok" else "🔴"
    parts = [f"{_LABELS[k]} {v}" for k, v in summary.items() if k in _LABELS and v not in (0, "", None)]
    text = f"{icon} {name.upper()} · {status}"
    if parts:
        text += "\n" + " · ".join(parts)
    if summary.get("ai_error"):
        text += f"\nAI: {str(summary['ai_error'])[:120]}"
    return text


def draft_preview(headline: str, body: str, lane: str, reason: str, sources: list[str],
                  draft_id: int | None = None) -> None:
    """A draft that needs a human decision. Not posted until you tap Approve."""
    text = (f"🔴 NEEDS YOUR APPROVAL · {lane.upper()}\nWhy: {reason}\n\n{headline}\n\n{body}"
            f"\n\nSource: {', '.join(sources)}")[:3900]
    if draft_id is None or not load_config().get("approvals", {}).get("buttons", False):
        _send(text.replace("🔴 NEEDS YOUR APPROVAL", "🔴 DRAFT (not posted)"))
        return
    import json

    from .publish.telegram_channel import _call, optional_env
    try:
        keyboard = {"inline_keyboard": [[{"text": "Approve and post", "callback_data": f"a:{draft_id}"},
                                         {"text": "Reject", "callback_data": f"r:{draft_id}"}]]}
        _call("sendMessage", {"chat_id": optional_env("TELEGRAM_OWNER_ID"), "text": text,
                              "reply_markup": json.dumps(keyboard)})
    except Exception:  # noqa: BLE001 - fall back to a plain preview
        log.warning("preview with buttons not sent")
        _send(text)
