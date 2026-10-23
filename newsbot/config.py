"""Config and secret access.

Rules:
- Non-secret settings live in config.json.
- Secrets come only from environment variables (GitHub Actions secrets in production).
- Each platform module reads ONLY its own credentials via require_env().
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


class MissingSecret(RuntimeError):
    """Raised when a required credential is not set. Never includes values."""


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def reload_config() -> None:
    load_config.cache_clear()


def require_env(*names: str) -> dict[str, str]:
    """Return the named env vars, or raise MissingSecret listing only the names."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            values[name] = value
        else:
            missing.append(name)
    if missing:
        raise MissingSecret("Missing required environment variables: " + ", ".join(missing))
    return values


def optional_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def platform_enabled(platform: str, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("enable", {}).get(platform, False))


_SENSITIVE_HINTS = ("TOKEN", "SECRET", "PASSWORD", "KEY", "PAT")


def redact(text: str, extra_values: list[str] | None = None) -> str:
    """Strip any known secret values from a string before logging it."""
    out = text
    candidates = [v for k, v in os.environ.items() if any(h in k.upper() for h in _SENSITIVE_HINTS) and len(v) >= 8]
    candidates += [v for v in (extra_values or []) if v and len(v) >= 8]
    for value in sorted(set(candidates), key=len, reverse=True):
        out = out.replace(value, "***")
    return out
