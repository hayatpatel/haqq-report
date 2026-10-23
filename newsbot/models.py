"""Core data types shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Tier(str, Enum):
    """Source tiers (plan section 4.1)."""

    A = "A"  # primary: UN agencies, courts, official statements
    B = "B"  # established newsrooms
    C = "C"  # independent creators / journalists (labelled on-ground reports)
    D = "D"  # viral social media, never posts alone

    @property
    def rank(self) -> int:
        return {"A": 4, "B": 3, "C": 2, "D": 1}[self.value]


class Lane(str, Enum):
    """Safety lanes (plan section 7)."""

    GREEN = "green"  # auto post
    AMBER = "amber"  # two sources or Tier A, extra checks
    RED = "red"  # never auto-posts, human approval required


@dataclass
class Article:
    url: str
    title: str
    summary: str
    source_name: str
    tier: Tier
    published: datetime | None = None
    language: str = "en"
    translated: bool = False
    state_funded: bool = False

    @property
    def key(self) -> str:
        return self.url.strip().lower().rstrip("/")


@dataclass
class Claim:
    text: str
    attribution: str  # named source, e.g. "UN OCHA", "Gaza Health Ministry"
    source_tier: Tier
    article_url: str = ""
    status: str = "unverified"  # unverified | verified | on_ground | rejected
    verified_by: str = ""


@dataclass
class Draft:
    headline: str
    body: str
    claims: list[Claim] = field(default_factory=list)
    lane: Lane = Lane.AMBER
    category: str = "general"
    source_urls: list[str] = field(default_factory=list)
    label: str = ""  # e.g. "On-ground report: reported by NAME, not independently confirmed yet"
    flags: list[str] = field(default_factory=list)
