"""AI providers with failover (Groq first, then Gemini). Both have free tiers.

Keep calls batched and cached to stay inside free quotas.
Credentials: GROQ_API_KEY, GEMINI_API_KEY (either one is enough).
"""
from __future__ import annotations

import logging
from typing import Callable

import requests

from .config import load_config, optional_env, redact

log = logging.getLogger("haqq.ai")


class AIUnavailable(RuntimeError):
    pass


# (provider, model) pairs that answered "not found / not allowed" this process: skip them afterwards.
_DEAD: set[tuple[str, str]] = set()


def _detail(resp) -> str:
    """The provider's own error message (never contains our key), so failures are diagnosable."""
    try:
        body = resp.json()
        err = body.get("error", body)
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
    except ValueError:
        msg = resp.text
    return redact(str(msg))[:200]


def _groq(prompt: str, model: str) -> str:
    key = optional_env("GROQ_API_KEY")
    if not key:
        raise AIUnavailable("GROQ_API_KEY not set")
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "temperature": 0.1, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    if not resp.ok:
        raise ModelError(f"groq {model}: HTTP {resp.status_code}: {_detail(resp)}", resp.status_code)
    return resp.json()["choices"][0]["message"]["content"]


def _gemini(prompt: str, model: str) -> str:
    key = optional_env("GEMINI_API_KEY")
    if not key:
        raise AIUnavailable("GEMINI_API_KEY not set")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1}},
        timeout=60,
    )
    if not resp.ok:
        raise ModelError(f"gemini {model}: HTTP {resp.status_code}: {_detail(resp)}", resp.status_code)
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


class ModelError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _models(cfg: dict, provider: str) -> list[str]:
    """Ordered model candidates; falls back to the old single-name setting."""
    listed = cfg.get(f"{provider}_models")
    return list(listed) if listed else [cfg[f"{provider}_model"]]


def _try_models(provider: str, call: Callable[[str, str], str], models: list[str], prompt: str) -> str:
    errors = []
    for model in models:
        if (provider, model) in _DEAD:
            continue
        try:
            return call(prompt, model)
        except ModelError as exc:
            errors.append(str(exc))
            if exc.status in (400, 403, 404):  # model missing / not allowed for this key: never retry it
                _DEAD.add((provider, model))
            log.warning("%s", exc)
    raise ModelError("; ".join(errors) or f"{provider}: no usable model left")


def generate(prompt: str, providers: dict[str, Callable[[str], str]] | None = None) -> str:
    """Try providers in configured order, each over its model list; raise AIUnavailable if all fail."""
    cfg = load_config()["ai"]
    providers = providers or {
        "groq": lambda p: _try_models("groq", _groq, _models(cfg, "groq"), p),
        "gemini": lambda p: _try_models("gemini", _gemini, _models(cfg, "gemini"), p),
    }
    errors = []
    for name in cfg["provider_order"]:
        fn = providers.get(name)
        if not fn:
            continue
        try:
            return fn(prompt)
        except Exception as exc:  # noqa: BLE001 - failover is the point
            errors.append(f"{name}: {redact(str(exc))[:400]}")
            log.warning("AI provider %s failed: %s", name, errors[-1])
    raise AIUnavailable("all AI providers failed: " + "; ".join(errors))


def list_models() -> dict[str, list[str]]:
    """Model IDs each key can actually use (for `ai-check`). Errors are returned as one string entry."""
    out: dict[str, list[str]] = {}
    gk, mk = optional_env("GROQ_API_KEY"), optional_env("GEMINI_API_KEY")
    if gk:
        try:
            r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {gk}"}, timeout=30)
            out["groq"] = sorted(m["id"] for m in r.json().get("data", [])) if r.ok else [f"HTTP {r.status_code}: {_detail(r)}"]
        except Exception as exc:  # noqa: BLE001
            out["groq"] = [f"{type(exc).__name__}"]
    if mk:
        try:
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": mk},
                             params={"pageSize": 200}, timeout=30)
            out["gemini"] = (sorted(m["name"].split("/")[-1] for m in r.json().get("models", [])
                                    if "generateContent" in m.get("supportedGenerationMethods", []))
                             if r.ok else [f"HTTP {r.status_code}: {_detail(r)}"])
        except Exception as exc:  # noqa: BLE001
            out["gemini"] = [f"{type(exc).__name__}"]
    return out
