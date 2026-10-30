"""Shared retry, logging and safe-failure rules for publishers.

Isolation rules (docs/meta-setup.md section 8):
- Each publisher runs inside its own try/except; one failure never blocks the others.
- A platform that fails N times in a row is auto-paused (that platform only) and an alert is raised.
- Secrets never reach logs: every message goes through config.redact().
"""
from __future__ import annotations

import json
import re
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from ..config import ROOT, load_config, redact

log = logging.getLogger("haqq.publish")

DEFAULT_TIMEOUT = 30
STATE_PATH = ROOT / "data" / "runtime_state.json"


@dataclass
class PublishResult:
    platform: str
    ok: bool
    post_id: str = ""
    error: str = ""
    skipped: bool = False
    extra: dict = field(default_factory=dict)


class PublishError(RuntimeError):
    pass


def graph_request(method: str, url: str, *, params: dict | None = None, data: dict | None = None,
                  timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Call a Meta/Threads Graph endpoint. Raises PublishError with a redacted message."""
    try:
        resp = requests.request(method, url, params=params, data=data, timeout=timeout)
    except requests.RequestException as exc:
        raise PublishError(redact(f"network error: {exc}")) from None
    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text[:300]}
    if resp.status_code >= 400 or "error" in payload:
        err = payload.get("error", payload)
        raise PublishError(redact(f"HTTP {resp.status_code}: {json.dumps(err)[:400]}"))
    return payload


_RATE = re.compile(r'"code":\s*(4|17|32|613|80001|80002|80003|80004|80005|80006|80007|80008|80009|80014)\b|request limit|rate.?limit|too many calls|reduce the amount of data', re.IGNORECASE)


def is_rate_limit(text: str) -> bool:
    """True for Meta's throttling errors (app, user or page call limits). They clear by themselves: wait, do not pause."""
    return bool(_RATE.search(text or ""))


class JsonStateStore:
    """Tiny file-backed store for failure counters. Production can swap in Supabase."""

    def __init__(self, path: Path = STATE_PATH):
        self.path = path

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def get(self, platform: str) -> dict:
        return self._read().get(platform, {"failures": 0, "paused": False})

    def set(self, platform: str, value: dict) -> None:
        state = self._read()
        state[platform] = value
        self._write(state)


def run_platform(platform: str, fn: Callable[[], str], *, store: JsonStateStore | None = None,
                 alert: Callable[[str], None] | None = None, retries: int = 1,
                 sleep: Callable[[float], None] = time.sleep) -> PublishResult:
    """Run one platform's publish function in isolation.

    - Paused platforms are skipped.
    - One retry on failure, then record the failure.
    - After PLATFORM_FAILURES_BEFORE_PAUSE consecutive failures: pause that platform and alert.
    """
    store = store or JsonStateStore()
    cfg = load_config()
    limit = cfg["limits"]["PLATFORM_FAILURES_BEFORE_PAUSE"]
    state = store.get(platform)

    if state.get("paused"):
        waited = time.time() - float(state.get("paused_at", 0))
        if waited < cfg["limits"].get("PLATFORM_PAUSE_MINUTES", 30) * 60:
            return PublishResult(platform, ok=False, skipped=True,
                                 error="platform paused. Last error: " + str(state.get("last_error", "unknown"))[:300])
        state = {"failures": 0, "paused": False}  # pause over: try again (a long-running listener must not stay paused for hours)

    last_error = ""
    for attempt in range(retries + 1):
        try:
            post_id = fn()
            store.set(platform, {"failures": 0, "paused": False})
            return PublishResult(platform, ok=True, post_id=str(post_id))
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            last_error = redact(str(exc))
            log.warning("%s publish attempt %d failed: %s", platform, attempt + 1, last_error)
            if attempt < retries:
                sleep(2)

    if is_rate_limit(last_error):  # throttled, not broken: no failure count, no pause; the caller sets a cooldown
        return PublishResult(platform, ok=False, error="rate limited: " + last_error)
    failures = int(state.get("failures", 0)) + 1
    paused = failures >= limit
    store.set(platform, {"failures": failures, "paused": paused, "last_error": last_error[:400], "paused_at": time.time()})
    if alert:
        msg = f"[{platform}] publish failed ({failures}/{limit}): {last_error}"
        if paused:
            msg += f"\n{platform} paused for the rest of this run after {limit} failures; it is tried again at the next scheduled run."
        try:
            alert(msg)
        except Exception:  # noqa: BLE001
            log.exception("alert failed")
    return PublishResult(platform, ok=False, error=last_error)
