"""Safety lanes and content checks (plan sections 6 and 7).

These are deterministic backstops that run on every draft after the AI writes it.
They are intentionally conservative: when in doubt, route to the Red lane (human approval).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import load_config
from .models import Draft, Lane

# --- Number normalisation (plan 4.5): Eastern Arabic digits -> ASCII -------------------------
_DIGIT_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)
_NUMBER_RE = re.compile(r"\d[\d,\.]*")


def normalise_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def extract_numbers(text: str) -> set[str]:
    nums = set()
    for raw in _NUMBER_RE.findall(normalise_digits(text)):
        cleaned = raw.replace(",", "").rstrip(".")
        if cleaned:
            nums.add(cleaned)
    return nums


def numbers_match(source_text: str, draft_text: str) -> tuple[bool, set[str]]:
    """Every number in the draft must appear in the source. Returns (ok, unmatched)."""
    src = extract_numbers(source_text)
    unmatched = extract_numbers(draft_text) - src
    return (not unmatched), unmatched


# --- Language policy -------------------------------------------------------------------------
# The desk never says these in its own voice; it quotes who said so (ICJ, UN Commission, ...).
LEGAL_TERMS = ("genocide", "ethnic cleansing", "war crime", "war crimes", "crimes against humanity")
QUOTE_ATTRIBUTION_HINTS = (
    "said", "says", "stated", "found", "ruled", "concluded", "described", "alleged",
    "according to", "reported", "warned", "determined", "has accused", "accused",
)
COLLECTIVE_BLAME_PATTERNS = [
    r"\ball (jews|muslims|christians|hindus|israelis|palestinians|arabs)\b",
    r"\b(jews|muslims|hindus|israelis|palestinians|arabs) are (all |always |inherently )?(evil|terrorists|animals|vermin|subhuman)\b",
    r"\bthose people\b",
]
CASUALTY_WORDS = r"(killed|dead|deaths|died|wounded|injured|casualties|martyred|fatalities|missing)"
CASUALTY_FIGURE_RE = re.compile(
    rf"\b\d[\d,\.]*\s*(?:\w+\s+){{0,3}}{CASUALTY_WORDS}\b|{CASUALTY_WORDS}\s+(?:\w+\s+){{0,3}}\d[\d,\.]*\b",
    re.IGNORECASE,
)

RED_KEYWORDS = {
    "child_victims": ["child victim", "children killed", "killed children", "orphan", "infant", "toddler", "schoolchildren killed"],
    "graphic": ["graphic", "footage of bodies", "mutilated", "beheaded", "corpse", "dismembered"],
    "legal_wording": list(LEGAL_TERMS),
    "detainees_hostages": ["detainee", "hostage", "prisoner", "captive", "abducted"],
    "unverified_viral": ["unverified video", "viral video", "viral clip", "circulating on social media", "unconfirmed footage"],
    "sectarian": ["sunni", "shia", "shi'a", "ahmadi", "sectarian", "takfir", "rafidi"],
    "call_to_action": ["donate now", "boycott", "take to the streets", "rise up", "join the protest"],
    "kashmir_xinjiang_political": ["kashmir", "xinjiang", "uyghur", "uighur", "article 370"],
}
AMBER_KEYWORDS = [
    "killed", "death toll", "casualties", "hate crime", "court", "ruling", "verdict", "arrested",
    "charged", "accused", "lawmakers", "parliament", "ban", "bill", "law", "election", "mosque attack",
]


@dataclass
class CheckResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _contains(text: str, needle: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text, re.IGNORECASE) is not None


def classify_lane(draft: Draft, cfg: dict | None = None) -> tuple[Lane, list[str]]:
    """Return the safety lane plus the reasons that decided it."""
    cfg = cfg or load_config()
    text = f"{draft.headline}\n{draft.body}".lower()
    reasons: list[str] = []

    for reason, words in RED_KEYWORDS.items():
        if any(_contains(text, w) for w in words):
            reasons.append(reason)

    designated = cfg.get("designated_group_keywords", [])
    if any(_contains(text, g) for g in designated):
        reasons.append("designated_group_mention")

    if draft.category in {"red", "graphic"}:
        reasons.append("category_forced_red")

    if reasons:
        return Lane.RED, reasons

    if any(_contains(text, w) for w in AMBER_KEYWORDS) or draft.category in {"casualty", "hate_crime", "court", "politics"}:
        return Lane.AMBER, ["amber_topic"]

    return Lane.GREEN, []


def check_draft(draft: Draft, source_text: str = "", cfg: dict | None = None) -> CheckResult:
    """Run all post-draft checks. Any failure -> retry once, then skip (plan section 6)."""
    cfg = cfg or load_config()
    failures: list[str] = []
    warnings: list[str] = []
    full = f"{draft.headline}\n{draft.body}"

    # 1. Every claim has a named attribution.
    if not draft.claims:
        failures.append("no claims extracted")
    for claim in draft.claims:
        if not claim.attribution.strip():
            failures.append(f"claim without named attribution: {claim.text[:60]}")

    # 2. Attribution must actually appear in the published text.
    if draft.claims and not any(c.attribution.strip() and c.attribution.lower() in full.lower() for c in draft.claims):
        failures.append("no attribution named in the post text")

    # 3. Legal terms never in the desk's own voice.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", full):
        for term in LEGAL_TERMS:
            if _contains(sentence, term):
                attributed = any(h in sentence.lower() for h in QUOTE_ATTRIBUTION_HINTS)
                if not attributed:
                    failures.append(f"'{term}' used without naming who said so")

    # 4. Collective blame.
    for pattern in COLLECTIVE_BLAME_PATTERNS:
        if re.search(pattern, full, re.IGNORECASE):
            failures.append("collective-blame wording")
            break

    # 5. Casualty figures need a named source in the same sentence.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", full):
        if CASUALTY_FIGURE_RE.search(sentence):
            attributed = any(h in sentence.lower() for h in QUOTE_ATTRIBUTION_HINTS) or any(
                c.attribution and c.attribution.lower() in sentence.lower() for c in draft.claims
            )
            if not attributed:
                failures.append(f"casualty figure without named source: {sentence.strip()[:70]}")

    # 6. Praise of designated organisations (heuristic, backs up the Red lane).
    designated = cfg.get("designated_group_keywords", [])
    praise = ("hero", "heroic", "glorious", "martyr leader", "salute", "proud of", "long live")
    for group in designated:
        if _contains(full, group) and any(_contains(full, p) for p in praise):
            failures.append("possible praise of a designated organisation")
            break

    # 7. Numbers must match the source text when we have it.
    if source_text:
        ok, unmatched = numbers_match(source_text, full)
        if not ok:
            failures.append("numbers not found in source: " + ", ".join(sorted(unmatched)))

    # 8. Tone: at most 2 emoji.
    emoji = re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", full)
    if len(emoji) > 2:
        warnings.append("more than 2 emoji")

    return CheckResult(ok=not failures, failures=failures, warnings=warnings)
