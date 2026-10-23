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
    from . import fbtoken
    try:
        data = meta_fb.debug_page_token()
    except (MissingSecret, PublishError) as exc:
        return f"FAIL facebook: {exc}"
    if not data.get("is_valid", False):
        return "FAIL facebook: token invalid (send /fbtoken to the bot to replace it)"
    exp_text, never = fbtoken.expiry_text(data)
    scopes = data.get("scopes") or []
    missing = [p for p in fbtoken.MUST + fbtoken.SHOULD if p not in scopes]
    line = f"token valid, expires: {exp_text}"
    if missing:
        line += "; missing permissions: " + ", ".join(missing)
    if not never or any(p in missing for p in fbtoken.MUST):
        return "FAIL facebook: " + line
    return ("WARN" if missing else "OK") + " facebook: " + line


def check_age_based(platform: str, label: str, refresh_fn, secret_name: str, store, now: datetime) -> str:
    cfg = load_config()
    if not optional_env(secret_name):
        return f"SKIP {label}: {secret_name} not set"
    if store is None:
        return f"FAIL {label}: Supabase is not configured, so the token age is unknown"
    try:
        issued = store.token_issued_at(platform)
    except Exception as exc:  # noqa: BLE001 - e.g. tables not created yet; report it, do not crash the job
        from .store import describe_http_error
        return (f"FAIL {label}: cannot read token age from Supabase [{describe_http_error(exc)}]. "
                "Run `python -m newsbot db-check` (Actions: db-check) for details")
    if issued is None:
        # First run: the token cannot tell us its age. Tokens are normally generated just before setup,
        # so record today as the issue date. Correct it with `token-issued` if the token is older.
        try:
            store.record_token_refresh(platform, now)
        except Exception as exc:  # noqa: BLE001
            from .store import describe_http_error
            return f"FAIL {label}: could not record the token date in Supabase [{describe_http_error(exc)}]"
        return f"OK {label}: first run, token age recorded as 0 days today (use `token-issued {platform} --days-ago N` if older)"
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
