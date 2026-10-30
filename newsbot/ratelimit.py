"""Cooldown after Meta throttles us, kept in the database so every workflow respects it.

When a platform answers with a call-limit error the bot stops calling that platform for `rate_limit_cooldown_minutes`
(default 60) instead of hammering it, and the missed posts are caught up later (runner.retry_missing_platforms).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def cooldown_until(store, family: str, now: datetime | None = None) -> datetime | None:
    if store is None:
        return None
    try:
        raw = store.get_setting(f"cooldown:{family}", "")
        until = datetime.fromisoformat(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None
    now = now or datetime.now(timezone.utc)
    return until if until and until > now else None


def start_cooldown(store, family: str, cfg: dict, now: datetime | None = None) -> None:
    if store is None:
        return
    minutes = cfg.get("limits", {}).get("rate_limit_cooldown_minutes", 60)
    try:
        store.set_setting(f"cooldown:{family}", ((now or datetime.now(timezone.utc)) + timedelta(minutes=minutes)).isoformat())
    except Exception:  # noqa: BLE001
        pass
