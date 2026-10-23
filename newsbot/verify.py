"""Tier and verification rules (plan sections 4.1 and 4.3)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Claim, Tier

# Tier C/D claims may be upgraded when ANY ONE of these is true (plan 4.3).
VERIFICATION_GROUPS = (
    "bellingcat",
    "forensic architecture",
    "afp fact check",
    "bbc verify",
    "reuters fact check",
)

AUTO_REJECT_REASONS = (
    "video_older_than_event",
    "ai_generated_image",
    "photo_from_another_war",
    "screenshot_without_original_link",
)


@dataclass
class Evidence:
    """What we know that could upgrade a Tier C/D claim."""

    tier_a_confirms: bool = False
    independent_b_or_c_reporters: int = 0
    geolocated_by: str = ""  # name of verification group, if any
    creator_score: float = 0.0  # 0-10
    low_risk: bool = False
    reject_flags: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    publishable: bool
    status: str  # verified | on_ground | held | rejected
    label: str = ""
    reason: str = ""


def on_ground_label(reporter: str) -> str:
    return f"On-ground report: reported by {reporter}, not independently confirmed yet"


def is_upgraded(ev: Evidence) -> bool:
    """True if ANY ONE upgrade condition holds and nothing triggers rejection."""
    if ev.reject_flags:
        return False
    if ev.tier_a_confirms:
        return True
    if ev.independent_b_or_c_reporters >= 2:
        return True
    if ev.geolocated_by and ev.geolocated_by.strip().lower() in VERIFICATION_GROUPS:
        return True
    if ev.creator_score >= 9 and ev.low_risk:
        return True
    return False


def evaluate_claim(claim: Claim, ev: Evidence | None = None, min_tier: Tier = Tier.C) -> Verdict:
    """Decide whether one claim may be published, and under what label."""
    ev = ev or Evidence()

    if ev.reject_flags:
        return Verdict(False, "rejected", reason="auto-reject: " + ", ".join(ev.reject_flags))

    if not claim.attribution.strip():
        return Verdict(False, "held", reason="no named attribution")

    if claim.source_tier in (Tier.A, Tier.B):
        return Verdict(True, "verified")

    if claim.source_tier == Tier.C:
        if is_upgraded(ev):
            return Verdict(True, "verified", reason="upgraded by corroboration")
        if min_tier.rank <= Tier.C.rank:
            return Verdict(True, "on_ground", label=on_ground_label(claim.attribution))
        return Verdict(False, "held", reason="tier C below VERIFY_MIN_TIER")

    # Tier D: never alone.
    if is_upgraded(ev):
        return Verdict(True, "verified", reason="upgraded by corroboration")
    return Verdict(False, "held", reason="tier D needs verification (plan 4.3)")


def evaluate_claims(claims: list[Claim], evidence: dict[str, Evidence] | None = None,
                    min_tier: Tier = Tier.C) -> tuple[bool, list[Verdict]]:
    """A draft is publishable only if every claim is publishable."""
    evidence = evidence or {}
    verdicts = [evaluate_claim(c, evidence.get(c.text), min_tier) for c in claims]
    return (bool(verdicts) and all(v.publishable for v in verdicts)), verdicts
