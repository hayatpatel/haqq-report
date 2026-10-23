"""Claim extraction and attribution (plan section 6, step 3).

The AI proposes claims as JSON; deterministic code validates them. Every claim must trace to the
source text, and every claim must carry a NAMED attribution.
"""
from __future__ import annotations

import json
import re

from .models import Article, Claim, Tier

CLAIM_PROMPT = """You extract factual claims from a news item for a fact-first news desk.
Return ONLY a JSON array. Each item: {{"text": str, "attribution": str}}.
Rules:
- "attribution" must be the NAMED source of the claim as stated in the text (e.g. "UN OCHA",
  "the Gaza Health Ministry", "journalist Jane Doe"). If the text names no source, use "".
- Never add facts that are not in the text. Never merge numbers.
- Never use the words genocide, ethnic cleansing or war crime unless quoting a named body.

TITLE: {title}
SOURCE: {source}
TEXT: {text}
"""


def build_prompt(article: Article) -> str:
    return CLAIM_PROMPT.format(title=article.title, source=article.source_name, text=article.summary[:4000])


def parse_claims(raw: str, article: Article) -> list[Claim]:
    """Parse the model's JSON. Tolerates code fences. Returns [] on malformed output."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        items = json.loads(cleaned)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    claims = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        attribution = str(item.get("attribution", "")).strip() or article.source_name
        claims.append(Claim(text=text, attribution=attribution, source_tier=article.tier, article_url=article.url))
    return claims


def traces_to_source(claim: Claim, article: Article, min_overlap: float = 0.6) -> bool:
    """Cheap anti-hallucination check: most content words of the claim must appear in the source text."""
    words = [w for w in re.findall(r"[a-z0-9]+", claim.text.lower()) if len(w) > 3]
    if not words:
        return False
    haystack = f"{article.title} {article.summary}".lower()
    hits = sum(1 for w in words if w in haystack)
    return hits / len(words) >= min_overlap


def extract_claims(article: Article, ai_call) -> list[Claim]:
    """ai_call(prompt) -> str. Keeps only claims that trace back to the article text."""
    claims = parse_claims(ai_call(build_prompt(article)), article)
    return [c for c in claims if traces_to_source(c, article)]
