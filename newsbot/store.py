"""Minimal Supabase (PostgREST) client. Credentials: SUPABASE_URL, SUPABASE_KEY.

Free tier: 500 MB database, 1 GB storage, and the project pauses if idle (see keepalive workflow).
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from .config import require_env


class Store:
    def __init__(self, url: str | None = None, key: str | None = None):
        if url is None or key is None:
            env = require_env("SUPABASE_URL", "SUPABASE_KEY")
            url, key = env["SUPABASE_URL"], env["SUPABASE_KEY"]
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def select(self, table: str, **filters) -> list[dict]:
        params = {k: f"eq.{v}" for k, v in filters.items()}
        resp = requests.get(f"{self.base}/{table}", headers=self.headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def insert(self, table: str, row: dict) -> dict:
        headers = {**self.headers, "Prefer": "return=representation"}
        resp = requests.post(f"{self.base}/{table}", headers=headers, json=row, timeout=30)
        resp.raise_for_status()
        return resp.json()[0]

    def upsert(self, table: str, row: dict, on_conflict: str) -> dict:
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
        resp = requests.post(f"{self.base}/{table}", headers=headers, params={"on_conflict": on_conflict},
                             json=row, timeout=30)
        resp.raise_for_status()
        return resp.json()[0]

    def update(self, table: str, match: dict, values: dict) -> None:
        params = {k: f"eq.{v}" for k, v in match.items()}
        resp = requests.patch(f"{self.base}/{table}", headers=self.headers, params=params, json=values, timeout=30)
        resp.raise_for_status()

    # --- token bookkeeping (token age cannot be read from the token itself) ---
    def token_issued_at(self, platform: str) -> datetime | None:
        rows = self.select("tokens", platform=platform)
        if not rows or not rows[0].get("issued_at"):
            return None
        return datetime.fromisoformat(rows[0]["issued_at"].replace("Z", "+00:00"))

    def record_token_refresh(self, platform: str, when: datetime | None = None) -> None:
        when = (when or datetime.now(timezone.utc)).isoformat()
        self.upsert("tokens", {"platform": platform, "issued_at": when, "last_refreshed": when}, "platform")
