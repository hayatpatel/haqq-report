"""How urgent is a story? Rule-based (no AI quota, instant). 0 = routine, 1 = notable, 2 = high, 3 = critical.

Used to rank stories in every run and to decide what the fast track posts at once.
"""
from __future__ import annotations

import re

R = re.IGNORECASE
CRITICAL = [
    ("massacre", r"\b(massacre|mass[- ]casualty|mass grave)\b"),
    ("ceasefire", r"\b(ceasefire|cease-fire|truce)\b.{0,60}\b(announc\w*|agree\w*|collaps\w*|breaks? down|violat\w*|takes? effect|ends?|ended|signed)\b"),
    ("ceasefire", r"\b(announc\w*|agree\w*|collaps\w*|violat\w*)\b.{0,60}\b(ceasefire|cease-fire|truce)\b"),
    ("court_ruling", r"\b(ICJ|ICC|International Court of Justice|International Criminal Court)\b.{0,80}\b(rul\w*|order\w*|warrant|verdict|judgment|decision|opinion)\b"),
    ("famine", r"\bfamine\b.{0,40}\b(declared|confirmed)\b|\bIPC\b.{0,40}famine"),
    ("mosque_attack", r"\b(mosque|masjid|worshippers?|worshipers?)\b.{0,60}\b(attack\w*|bomb\w*|set on fire|arson|shooting|shot|massacre|burn\w*|stabb\w*)\b"),
    ("mosque_attack", r"\b(attack\w*|bomb\w*|arson|shooting|massacre)\b.{0,40}\b(mosque|masjid|worshippers?|worshipers?)\b"),
    ("assassination", r"\b(assassinat\w*|coup|declares? war|state of emergency)\b"),
    ("protected_site", r"\b(hospital|school|shelter|refugee camp|aid convoy|aid workers?|UN staff|journalists?)\b.{0,50}\b(struck|hit|bombed|shelled|killed|attacked)\b"),
]
HIGH = [
    ("strike", r"\b(air ?strikes?|shelling|bombardment|drone strike|raid)\b"),
    ("evacuation", r"\b(evacuation orders?|forced to flee|displaced)\b"),
    ("aid", r"\b(aid (blocked|blockade)|blockade|siege|famine|malnutrition|cholera|outbreak)\b"),
    ("diplomacy", r"\b(Security Council|General Assembly|sanctions|arrest warrant|peace talks|negotiations)\b"),
    ("hate_crime", r"\b(hate crime|islamophob\w*|anti-Muslim|hijab ban|niqab ban|burqa ban|Muslim ban|Quran (burn\w*|desecrat\w*)|vandalis\w*)\b"),
    ("settler_violence", r"\b(settlers?|demolitions?|home demolition|eviction)\b"),
    ("detention", r"\b(detain\w*|arrest\w*|imprison\w*|hostages?|prisoners?)\b"),
    ("disaster", r"\b(earthquake|tsunami|cyclone|floods?|landslide)\b"),
]
_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"
CASUALTY_RE = re.compile(
    rf"(?:{_NUM}\s+(?:\w+\s+){{0,3}}(?:were\s+)?(?:killed|dead|died|martyred|lives lost))|"
    rf"(?:(?:killed|killing|kills|death toll(?:\s+(?:rises|climbs|reaches|hits|jumps|stands))?(?:\s+(?:to|at|of))?|dead:?)\s+(?:at least\s+|more than\s+|over\s+)?{_NUM})", R)


def casualties(text: str) -> int:
    """Largest casualty number mentioned next to killed/dead/death toll (0 if none)."""
    best = 0
    for m in CASUALTY_RE.finditer(text):
        for g in m.groups():
            if g:
                best = max(best, int(g.replace(",", "")))
    return best if best < 1_000_000 else 0


def severity(text: str) -> tuple[int, list[str]]:
    """(level 0-3, reasons)."""
    level, reasons = 0, []
    for name, pat in CRITICAL:
        if re.search(pat, text, R):
            level, reasons = 3, reasons + [name]
    n = casualties(text)
    if n >= 20:
        level, reasons = 3, reasons + [f"casualties>={n}"]
    elif n >= 5:
        level, reasons = max(level, 2), reasons + [f"casualties>={n}"]
    for name, pat in HIGH:
        if re.search(pat, text, R):
            level, reasons = max(level, 2), reasons + [name]
    if level == 0 and re.search(r"\b(killed|dead|wounded|injured|attack\w*|crisis|emergency)\b", text, R):
        level = 1
    return level, list(dict.fromkeys(reasons))
