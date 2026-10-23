"""Token expiry checks and refresh for Instagram and Threads (60-day tokens).

The Facebook Page token does not expire; it is only verified with debug_token.
Token age cannot be read from the token itself, so issued_at is stored (Supabase `tokens` table).
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

import requests

from ..config import load_config, require_env
from .base import PublishError, graph_request


def days_since(issued_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    return (now - issued_at).total_seconds() / 86400


def needs_refresh(issued_at: datetime, now: datetime | None = None, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return days_since(issued_at, now) >= cfg["meta"]["token_refresh_after_days"]


def days_until_expiry(issued_at: datetime, now: datetime | None = None, cfg: dict | None = None) -> float:
    cfg = cfg or load_config()
    return cfg["meta"]["token_lifetime_days"] - days_since(issued_at, now)


def should_alert(issued_at: datetime, now: datetime | None = None, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return days_until_expiry(issued_at, now, cfg) <= cfg["meta"]["token_alert_days_before_expiry"]


def refresh_instagram() -> str:
    """Return a new 60-day Instagram token (token must be >24h old and unexpired)."""
    env = require_env("IG_TOKEN")
    out = graph_request("GET", "https://graph.instagram.com/refresh_access_token",
                        params={"grant_type": "ig_refresh_token", "access_token": env["IG_TOKEN"]})
    return out["access_token"]


def refresh_threads() -> str:
    """Return a new 60-day Threads token."""
    env = require_env("TH_TOKEN")
    out = graph_request("GET", "https://graph.threads.net/refresh_access_token",
                        params={"grant_type": "th_refresh_token", "access_token": env["TH_TOKEN"]})
    return out["access_token"]


def update_github_secret(name: str, value: str, repo: str | None = None) -> None:
    """Write a refreshed token back to GitHub Actions secrets (needs GH_PAT_SECRETS, PyNaCl)."""
    from nacl import encoding, public  # imported lazily so tests do not need it

    env = require_env("GH_PAT_SECRETS")
    repo = repo or require_env("GITHUB_REPOSITORY")["GITHUB_REPOSITORY"]
    headers = {"Authorization": f"Bearer {env['GH_PAT_SECRETS']}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{repo}/actions/secrets"

    key_resp = requests.get(f"{api}/public-key", headers=headers, timeout=30)
    if key_resp.status_code != 200:
        raise PublishError(f"could not fetch repo public key: HTTP {key_resp.status_code}")
    key = key_resp.json()

    sealed = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    encrypted = base64.b64encode(sealed.encrypt(value.encode())).decode()

    put = requests.put(f"{api}/{name}", headers=headers, timeout=30,
                       json={"encrypted_value": encrypted, "key_id": key["key_id"]})
    if put.status_code not in (201, 204):
        raise PublishError(f"could not update secret {name}: HTTP {put.status_code}")
