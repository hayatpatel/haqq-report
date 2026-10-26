"""Minimal Supabase (PostgREST) client. Credentials: SUPABASE_URL, SUPABASE_KEY.

Free tier: 500 MB database, 1 GB storage, and the project pauses if idle (see keepalive workflow).
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from .config import require_env

# Every table created by db/schema.sql.
REQUIRED_TABLES = ["sources", "creators", "articles", "claims", "factchecks", "drafts", "posts",
                   "corrections", "occasions", "tokens", "settings", "run_logs"]


def auth_headers(key: str) -> dict:
    """Headers for Supabase REST.

    New-style keys (sb_secret_..., sb_publishable_...) are NOT JWTs and must only go in `apikey`;
    sending them as `Authorization: Bearer` makes the request fail. Legacy JWT keys (anon/service_role)
    use both headers.
    """
    headers = {"apikey": key, "Content-Type": "application/json"}
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def describe_http_error(exc: Exception) -> str:
    """Short, secret-free description: 'HTTP 401: message (code)'. Safe to send to Telegram."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return f"{type(exc).__name__}: {str(exc)[:120]}"
    try:
        body = resp.json()
        detail = f"{str(body.get('message', ''))[:140]} ({body.get('code', '')})".strip()
    except ValueError:
        detail = (resp.text or "")[:140]
    return f"HTTP {resp.status_code}: {detail}"


class Store:
    def __init__(self, url: str | None = None, key: str | None = None):
        if url is None or key is None:
            env = require_env("SUPABASE_URL", "SUPABASE_KEY")
            url, key = env["SUPABASE_URL"], env["SUPABASE_KEY"]
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = auth_headers(key)

    def select(self, table: str, **filters) -> list[dict]:
        params = {k: f"eq.{v}" for k, v in filters.items()}
        resp = requests.get(f"{self.base}/{table}", headers=self.headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def select_raw(self, table: str, params: dict) -> list[dict]:
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

    def delete(self, table: str, params: dict) -> int:
        """Delete matching rows; returns how many were removed."""
        headers = {**self.headers, "Prefer": "return=representation"}
        resp = requests.delete(f"{self.base}/{table}", headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return len(resp.json())

    def claim(self, table: str, row_id, from_status: str, to_status: str, extra: dict | None = None) -> bool:
        """Atomically move a row from one status to another. True only for the ONE caller that wins the race,
        so two workflows can never both post the same draft."""
        headers = {**self.headers, "Prefer": "return=representation"}
        resp = requests.patch(f"{self.base}/{table}", headers=headers,
                              params={"id": f"eq.{row_id}", "status": f"eq.{from_status}"},
                              json={"status": to_status, **(extra or {})}, timeout=30)
        resp.raise_for_status()
        return len(resp.json()) == 1

    def claim_flag(self, table: str, row_id, column: str, from_value, to_value) -> bool:
        """Atomically flip a boolean column (e.g. articles.processed false -> true). True for the one winner."""
        headers = {**self.headers, "Prefer": "return=representation"}
        resp = requests.patch(f"{self.base}/{table}", headers=headers,
                              params={"id": f"eq.{row_id}", column: f"eq.{str(from_value).lower()}"},
                              json={column: to_value}, timeout=30)
        resp.raise_for_status()
        return len(resp.json()) == 1

    def try_insert(self, table: str, row: dict) -> bool:
        """Insert; False when the primary key / unique index already exists. Used as a lock."""
        headers = {**self.headers, "Prefer": "return=minimal"}
        resp = requests.post(f"{self.base}/{table}", headers=headers, json=row, timeout=30)
        if resp.status_code == 409:
            return False
        resp.raise_for_status()
        return True

    def get_setting(self, key: str, default: str = "") -> str:
        rows = self.select_raw("settings", {"key": f"eq.{key}"})
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self.upsert("settings", {"key": key, "value": value}, "key")

    def cas_setting(self, key: str, old: str, new: str) -> bool:
        """Compare-and-swap on a settings value: True only if the value was still `old`."""
        headers = {**self.headers, "Prefer": "return=representation"}
        resp = requests.patch(f"{self.base}/settings", headers=headers, params={"key": f"eq.{key}", "value": f"eq.{old}"},
                              json={"value": new}, timeout=30)
        resp.raise_for_status()
        return len(resp.json()) == 1

    def check(self) -> list[tuple[str, bool, str]]:
        """Connection test: can we read every required table? Returns (table, ok, detail)."""
        out = []
        for table in REQUIRED_TABLES:
            try:
                resp = requests.get(f"{self.base}/{table}", headers=self.headers,
                                    params={"select": "*", "limit": "1"}, timeout=20)
                resp.raise_for_status()
                out.append((table, True, "ok"))
            except requests.RequestException as exc:
                out.append((table, False, describe_http_error(exc)))
        return out

    # --- token bookkeeping (token age cannot be read from the token itself) ---
    def token_issued_at(self, platform: str) -> datetime | None:
        rows = self.select("tokens", platform=platform)
        if not rows or not rows[0].get("issued_at"):
            return None
        return datetime.fromisoformat(rows[0]["issued_at"].replace("Z", "+00:00"))

    def record_token_refresh(self, platform: str, when: datetime | None = None) -> None:
        when = (when or datetime.now(timezone.utc)).isoformat()
        self.upsert("tokens", {"platform": platform, "issued_at": when, "last_refreshed": when}, "platform")
