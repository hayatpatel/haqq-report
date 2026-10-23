"""Bluesky publisher (text posts via the AT Protocol XRPC API).

Credentials: BSKY_HANDLE, BSKY_APP_PASSWORD (an app password, never the account password).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from ..config import redact, require_env
from .base import PublishError

PDS = "https://bsky.social/xrpc"
MAX_LEN = 300


def _post_json(url: str, body: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.post(url, data=json.dumps(body), headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise PublishError(redact(f"network error: {exc}")) from None
    if resp.status_code >= 400:
        raise PublishError(redact(f"HTTP {resp.status_code}: {resp.text[:300]}"))
    return resp.json()


def _session() -> dict:
    env = require_env("BSKY_HANDLE", "BSKY_APP_PASSWORD")
    return _post_json(f"{PDS}/com.atproto.server.createSession",
                      {"identifier": env["BSKY_HANDLE"], "password": env["BSKY_APP_PASSWORD"]})


def publish(text: str) -> str:
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1].rstrip() + "…"
    session = _session()
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    out = _post_json(f"{PDS}/com.atproto.repo.createRecord", {
        "repo": session["did"], "collection": "app.bsky.feed.post", "record": record,
    }, token=session["accessJwt"])
    return out["uri"]
