"""Myth vs Fact (plan section 9).

Flow: detect a viral claim -> use an existing fact-checker's verdict with credit, or do our own check
(Quran text, graded hadith, historical source, origin of media) and publish only with TWO reliable
sources. Religious content is reviewed by a scholar volunteer. The AI never gives religious rulings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Hadith grading must come from a graded source (e.g. sunnah.com); Quran text from verified translations.
ALLOWED_TRANSLATIONS = ("Pickthall", "Yusuf Ali")  # public domain; others only where licensed
RELIGIOUS_RULING_MARKERS = ("fatwa", "haram", "halal ruling", "it is obligatory", "tafsir", "the ruling is")


@dataclass
class FactCheck:
    claim: str
    verdict: str  # true | false | misleading | unproven
    evidence_urls: list[str] = field(default_factory=list)
    credited_checker: str = ""  # set when we reuse another fact-checker's verdict
    religious: bool = False
    scholar_reviewed: bool = False


def can_publish(fc: FactCheck) -> tuple[bool, str]:
    if fc.credited_checker:
        return (bool(fc.evidence_urls), "credited fact-checker verdict" if fc.evidence_urls else "no evidence link")
    if len(set(fc.evidence_urls)) < 2:
        return False, "own checks need two reliable sources; keep in watchlist"
    if fc.religious and not fc.scholar_reviewed:
        return False, "religious content needs scholar volunteer review"
    return True, "ok"


def contains_religious_ruling(text: str) -> bool:
    """Backstop: the desk never writes fatwas, tafsir or its own interpretation."""
    low = text.lower()
    return any(m in low for m in RELIGIOUS_RULING_MARKERS)


def render_card_text(fc: FactCheck) -> str:
    lines = [f"CLAIM: {fc.claim}", f"VERDICT: {fc.verdict.upper()}"]
    if fc.credited_checker:
        lines.append(f"Checked by: {fc.credited_checker}")
    lines += [f"Source: {u}" for u in fc.evidence_urls]
    return "\n".join(lines)
