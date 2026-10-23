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


def post_results(headline: str, results: Iterable, *, dry_run: bool, cfg: dict | None = None) -> None:
    """One summary message per post: where it went, and anything that failed."""
    if not _enabled("posts", cfg):
        return
    results = list(results)
    if not results:
        return
    ok = [r for r in results if r.ok and not r.skipped]
    failed = [r for r in results if not r.ok]
    title = "DRY RUN (nothing was published)" if dry_run else ("Posted" if ok else "NOT posted")
    lines = [f"{title}: {headline[:120]}"]
    for r in results:
        if r.skipped and r.ok:
            lines.append(f"  {r.platform}: would post")
        elif r.ok:
            lines.append(f"  {r.platform}: OK {r.post_id or ''}".rstrip())
        else:
            lines.append(f"  {r.platform}: FAILED {r.error or ''}".rstrip())
    if failed:
        lines.insert(0, "ATTENTION: some platforms failed")
    elif not dry_run and not ok:
        return
    _send("\n".join(lines))


def error(where: str, detail: str, cfg: dict | None = None) -> None:
    if _enabled("errors", cfg):
        _send(f"ERROR in {where}\n{detail[:3000]}")


def health_report(report: str, failed: bool, cfg: dict | None = None) -> None:
    """Daily status. Sent every day (not only on failure) when owner_feed.daily_health_report is true."""
    if failed or _enabled("daily_health_report", cfg):
        _send(("ACTION NEEDED\n" if failed else "") + report)


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


def draft_preview(headline: str, body: str, lane: str, reason: str, sources: list[str],
                  draft_id: int | None = None) -> None:
    """A draft that needs human review. NOT posted until you tap Approve (when draft_id is known)."""
    text = (f"DRAFT for review (not posted)\nLane: {lane.upper()} ({reason})\n\n{headline}\n\n{body}"
            f"\n\nSource: {', '.join(sources)}")[:3900]
    if draft_id is None:
        _send(text)
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
