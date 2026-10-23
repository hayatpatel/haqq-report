"""Story scoring (plan section 6).

score = 0.35*significance + 0.30*impact + 0.35*interest + bonuses
Inputs are 0-10. Bonuses:
  gap bonus   +0.8  Tier A/B/C reports it and fewer than 2 major wires carried it after GAP_HOURS
  watch topic +0.5  Palestine and priority crises reliably make the cut
Outrage never raises a score.
"""
from __future__ import annotations

from .config import load_config
from .models import Tier


def has_watch_topic(text: str, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    low = text.lower()
    return any(topic in low for topic in cfg.get("watch_topics", []))


def gap_applies(source_tier: Tier, wires_carrying: int, hours_since_published: float, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    gap_hours = cfg["limits"]["GAP_HOURS"]
    max_wires = cfg["scoring"]["gap_major_wires_max"]
    return (
        source_tier in (Tier.A, Tier.B, Tier.C)
        and hours_since_published >= gap_hours
        and wires_carrying < max_wires
    )


def score_story(significance: float, impact: float, interest: float, *, text: str = "",
                source_tier: Tier = Tier.B, wires_carrying: int = 99,
                hours_since_published: float = 0.0, cfg: dict | None = None) -> float:
    cfg = cfg or load_config()
    w = cfg["scoring"]["weights"]
    score = w["significance"] * significance + w["impact"] * impact + w["interest"] * interest
    if gap_applies(source_tier, wires_carrying, hours_since_published, cfg):
        score += cfg["scoring"]["gap_bonus"]
    if has_watch_topic(text, cfg):
        score += cfg["scoring"]["watch_topic_bonus"]
    return round(score, 3)
