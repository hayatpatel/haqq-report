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
    resp.raise_for_status()
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
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def generate(prompt: str, providers: dict[str, Callable[[str], str]] | None = None) -> str:
    """Try providers in configured order; raise AIUnavailable if all fail."""
    cfg = load_config()["ai"]
    providers = providers or {
        "groq": lambda p: _groq(p, cfg["groq_model"]),
        "gemini": lambda p: _gemini(p, cfg["gemini_model"]),
    }
    errors = []
    for name in cfg["provider_order"]:
        fn = providers.get(name)
        if not fn:
            continue
        try:
            return fn(prompt)
        except Exception as exc:  # noqa: BLE001 - failover is the point
            errors.append(f"{name}: {redact(str(exc))[:120]}")
            log.warning("AI provider %s failed: %s", name, errors[-1])
    raise AIUnavailable("all AI providers failed: " + "; ".join(errors))
