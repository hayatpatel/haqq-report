"""One draft, written separately for each platform (Facebook, Instagram, Threads), checked before use.

The facts come only from the verified draft. The AI is asked to change tone, length and structure for each platform's
audience and ranking signals; it may not add facts. Every variant is checked in code (numbers must exist in the draft, no
links, no emoji, no engagement bait such as "like if" or "tag a friend", the on-ground label survives); a variant that fails is
dropped and that platform falls back to the standard template body, so a bad rewrite can never go out.
"""
from __future__ import annotations

import json
import logging
import re

from . import safety
from .models import Draft

log = logging.getLogger("haqq.variants")
PLATFORMS = ("facebook", "instagram", "threads")
BAIT = re.compile(r"\b(like|share|comment|tag|follow|subscribe|repost|retweet)\b[^.\n]{0,25}\b(if|to win|below|now|a friend|your friends?|this post)\b|"
                  r"\b(tag|send this to)\s+(a|your|someone)\b|\bsmash\b|\bturn on notifications\b|\bwho else\b", re.IGNORECASE)
NUM = re.compile(r"\d[\d,.]*")

PROMPT = """You adapt ONE verified news item for three social platforms. Use ONLY the facts below. Do not add facts, names,
numbers, places or quotes. Keep every claim attributed exactly as in the verified text. Plain text only: no emoji, no hashtags,
no links, no markdown. Calm, factual, dignified tone; no sensational words.

Verified headline: {headline}
Verified text: {body}
Source: {source}

Write one version per platform:
- facebook: 2 short paragraphs, 350 to 650 characters. Explain what happened and why it matters. May end with ONE genuine, open,
  neutral question about the issue (not about reacting or sharing).
- instagram: the first sentence must work alone as a hook (under 110 characters) because only that line shows before "more".
  Then 2 to 3 short lines, 250 to 500 characters in total. No question asking people to like, share, comment or tag.
- threads: conversational, 150 to 260 characters in total, one clear point, may end with one open question.

Never write phrases such as "like if", "share this", "comment below", "tag a friend", "who else".
Return ONLY JSON: {{"facebook": "...", "instagram": "...", "threads": "..."}}"""


def _numbers(text: str) -> set[str]:
    return {n.strip(".,") for n in NUM.findall(text)}


def valid(text: str, draft: Draft, cap: int) -> bool:
    if not text or len(text) > cap or "http" in text.lower() or "#" in text or BAIT.search(text):
        return False
    if draft.label and draft.label.lower() not in text.lower():
        return False
    return _numbers(text) <= _numbers(draft.headline + " " + draft.body)


def adapt(draft: Draft, ai_call, cfg: dict, source: str = "") -> dict[str, str]:
    """{platform: body text} for the platforms whose rewrite passed every check. Never raises."""
    pc = cfg.get("platform_style", {})
    if not pc.get("enabled", True):
        return {}
    try:
        raw = ai_call(PROMPT.format(headline=draft.headline, body=draft.body[:2500], source=source or "the source"))
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception as exc:  # noqa: BLE001 - the standard template is the fallback
        log.warning("platform variants unavailable (%s)", type(exc).__name__)
        return {}
    out = {}
    for platform in PLATFORMS:
        text = safety.strip_emoji(str(data.get(platform, ""))).strip()
        if valid(text, draft, pc.get(platform, {}).get("max_chars", 700)):
            out[platform] = text
        else:
            log.info("%s rewrite rejected, using the standard body", platform)
    return out
