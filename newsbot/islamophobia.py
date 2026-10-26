"""Islamophobia Watch pillar (plan section 3): hate crimes, mosque attacks, discriminatory laws and bans."""
from __future__ import annotations

import re

PATTERNS = [
    r"\bislamophob\w*", r"\banti-?muslim\b", r"\bmuslim ban\b", r"\b(hijab|niqab|burqa|headscarf)\b.{0,40}\b(ban\w*|forbid\w*|prohibit\w*|attack\w*|torn|removed)\b",
    r"\b(mosque|masjid)\b.{0,50}\b(attack\w*|arson|vandali\w*|fire|bomb\w*|threat\w*|shooting|desecrat\w*|graffiti)\b",
    r"\b(attack\w*|arson|vandali\w*|bomb\w*|shooting|threat\w*|desecrat\w*|graffiti)\b.{0,40}\b(mosque|masjid)\b",
    r"\bQuran\b.{0,40}\b(burn\w*|desecrat\w*|torn)\b", r"\bhate crimes?\b.{0,60}\bmuslims?\b|\bmuslims?\b.{0,60}\bhate crimes?\b",
    r"\bmuslims?\b.{0,50}\b(lynch\w*|mob|beaten|assault\w*|harass\w*|discriminat\w*|evict\w*)\b",
    r"\b(Tell MAMA|CAIR)\b",
]
RULES = """- This is an Islamophobia Watch item. State what happened, where, and WHO reported it (police, a court, a named monitoring group).
- Do not speculate about motive, and do not name or describe a suspect unless police or a court did, in the text below.
- Never generalise about any religion, ethnicity or community, including the people accused.
- If the source is an advocacy group (CAIR, Tell MAMA), write "reported by <group>" and say that police have or have not confirmed it.
- Do not tell readers what to think or do. No call to action."""


def is_islamophobia(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in PATTERNS)
