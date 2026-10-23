"""Telegram: public channel posting and owner alerts.

Credentials: TELEGRAM_BOT_TOKEN (+ TELEGRAM_CHANNEL_ID for the channel, TELEGRAM_OWNER_ID for alerts).
Telegram is an owned audience, safe from Meta restrictions.
"""
from __future__ import annotations

from ..config import optional_env, require_env
from .base import PublishError, graph_request


def _api(method: str) -> str:
    token = require_env("TELEGRAM_BOT_TOKEN")["TELEGRAM_BOT_TOKEN"]
    return f"https://api.telegram.org/bot{token}/{method}"


def _call(method: str, data: dict, timeout: int = 30) -> dict:
    out = graph_request("POST", _api(method), data=data, timeout=timeout)
    if not out.get("ok", False):
        raise PublishError(f"telegram {method} failed")
    return out["result"]


def send_to_channel(text: str, image_url: str | None = None) -> str:
    chat_id = require_env("TELEGRAM_CHANNEL_ID")["TELEGRAM_CHANNEL_ID"]
    if image_url:
        res = _call("sendPhoto", {"chat_id": chat_id, "photo": image_url, "caption": text[:1024]})
    else:
        res = _call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "false"})
    return str(res["message_id"])


def alert_owner(text: str) -> None:
    """Send an alert to the owner. Silent no-op if Telegram is not configured yet."""
    if not (optional_env("TELEGRAM_BOT_TOKEN") and optional_env("TELEGRAM_OWNER_ID")):
        return
    _call("sendMessage", {"chat_id": optional_env("TELEGRAM_OWNER_ID"), "text": text[:4000]})
