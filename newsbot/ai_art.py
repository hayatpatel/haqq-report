"""Free AI illustration fallback (Cloudflare Workers AI, FLUX.1 schnell, Apache 2.0).

Used ONLY when no licensed photo exists. It is never a photo of a real event: the prompt is a fixed symbolic theme
(no people, no real places, no flags, no text), and the image is labelled "AI-generated illustration" on the image itself and in
the caption. Free plan only: Cloudflare stops at the daily allowance instead of charging. We also keep our own daily budget and
stop for the rest of the day when Cloudflare says the allowance is used up; the post then gets the plain brand card.
"""
from __future__ import annotations

import base64
import io
import logging
from datetime import datetime, timezone

import requests

from .config import optional_env, redact

log = logging.getLogger("haqq.ai_art")
MODEL = "@cf/black-forest-labs/flux-1-schnell"
LABEL = "AI-generated illustration"
STYLE = ("minimal abstract symbolic illustration, flat vector style, muted dark green and gold palette, soft light, "
         "no people, no faces, no text, no letters, no logos, no flags, no photorealism")
THEMES = {
    "GAZA": "a lone olive branch over broken geometric shapes at dawn",
    "PALESTINE": "a lone olive branch over broken geometric shapes at dawn",
    "SUDAN": "an empty dusty road leading to a pale sunrise",
    "YEMEN": "an empty dusty road leading to a pale sunrise",
    "SYRIA": "a single candle in a dark archway",
    "KASHMIR": "snow mountains and a quiet valley in mist",
    "ISLAMOPHOBIA": "a mosque dome silhouette under a calm night sky with a crescent moon",
    "BREAKING": "a bright beam of light cutting through dark abstract shapes",
    "SPOTLIGHT": "a warm spotlight on an empty stage",
}
DEFAULT_THEME = "a balanced scale of justice, abstract, geometric"


def _day() -> str:
    return f"ai:{datetime.now(timezone.utc):%Y%m%d}"


def configured() -> bool:
    return bool(optional_env("CLOUDFLARE_ACCOUNT_ID") and optional_env("CLOUDFLARE_API_TOKEN"))


def prompt_for(topic: str) -> str:
    return f"{THEMES.get(topic.upper(), DEFAULT_THEME)}, {STYLE}"


def _used(store) -> int:
    if store is None:
        return 0
    raw = store.get_setting(_day(), "0")
    return 999 if raw == "off" else int(raw or 0)


def generate(topic: str, cfg: dict, store=None) -> tuple[bytes | None, str]:
    """(image bytes or None, short note). Never raises."""
    ac = cfg.get("ai_art", {})
    if not ac.get("enabled", False):
        return None, "AI illustration off"
    if not configured():
        return None, "AI illustration not set up (no Cloudflare keys)"
    try:
        if _used(store) >= ac.get("max_per_day", 40):
            return None, "AI illustration: daily budget used, brand card instead"
        acct, token = optional_env("CLOUDFLARE_ACCOUNT_ID"), optional_env("CLOUDFLARE_API_TOKEN")
        resp = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{MODEL}",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"prompt": prompt_for(topic), "steps": 4}, timeout=90)
        if resp.status_code == 429 or "4006" in resp.text or "daily free allocation" in resp.text.lower():
            if store is not None:
                store.set_setting(_day(), "off")  # free allowance gone: stop trying until tomorrow (UTC)
            return None, "AI illustration: free daily allowance used up, brand card instead"
        resp.raise_for_status()
        data = base64.b64decode(resp.json()["result"]["image"])
        if store is not None:
            store.set_setting(_day(), str(_used(store) + 1))
        return data, "AI illustration (Cloudflare FLUX.1 schnell)"
    except Exception as exc:  # noqa: BLE001 - the brand card is always the fallback
        log.warning("AI illustration failed: %s", redact(str(exc))[:150])
        return None, f"AI illustration failed ({type(exc).__name__}), brand card instead"
