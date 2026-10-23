"""Daily health job: token status for all three Meta platforms (plan fix #7).

Facebook: debug_token (never expires). Instagram/Threads: age from Supabase `tokens`;
refresh after 40 days and alert 10 days before expiry. Any failure alerts the owner on Telegram.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import MissingSecret, load_config, optional_env
from .publish import meta_fb, tokens
from .publish.base import PublishError
from . import owner_feed
from .publish.telegram_channel import alert_owner  # noqa: F401 (kept for callers/tests)


def check_facebook() -> str:
    try:
        data = meta_fb.debug_page_token()
    except (MissingSecret, PublishError) as exc:
        return f"FAIL facebook: {exc}"
    if not data.get("is_valid", False):
        return "FAIL facebook: token invalid"
    expires = data.get("expires_at", 0)
    return "OK facebook: token valid" + (" (never expires)" if not expires else f" (expires_at={expires})")


def check_age_based(platform: str, label: str, refresh_fn, secret_name: str, store, now: datetime) -> str:
    cfg = load_config()
    if not optional_env(secret_name):
        return f"SKIP {label}: {secret_name} not set"
    issued = store.token_issued_at(platform) if store else None
    if issued is None:
        return f"WARN {label}: no issued_at recorded; record it once with `python -m newsbot token-issued {platform}`"
    age = tokens.days_since(issued, now)
    left = tokens.days_until_expiry(issued, now, cfg)
    if tokens.needs_refresh(issued, now, cfg):
        try:
            new_token = refresh_fn()
            tokens.update_github_secret(secret_name, new_token)
            store.record_token_refresh(platform, now)
            return f"OK {label}: refreshed (was {age:.0f} days old)"
        except (MissingSecret, PublishError, Exception) as exc:  # noqa: BLE001
            return f"FAIL {label}: refresh failed ({type(exc).__name__}); {left:.0f} days left, re-run OAuth"
    return f"OK {label}: {age:.0f} days old, {left:.0f} days left"


def run(store=None, now: datetime | None = None) -> tuple[list[str], bool]:
    """Return (status lines, any_failure). Alerts the owner on Telegram when something fails."""
    now = now or datetime.now(timezone.utc)
    lines = [
        check_facebook(),
        check_age_based("instagram", "instagram", tokens.refresh_instagram, "IG_TOKEN", store, now),
        check_age_based("threads", "threads", tokens.refresh_threads, "TH_TOKEN", store, now),
    ]
    failed = any(line.startswith("FAIL") for line in lines)
    report = "Haqq Report token health\n" + "\n".join(lines)
    owner_feed.health_report(report, failed or any(line.startswith("WARN") for line in lines))
    return lines, failed
