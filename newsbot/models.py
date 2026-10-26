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
    related_urls: list[str] = field(default_factory=list)  # other outlets covering the same item (celebrity digest)

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
    tags: list[str] = field(default_factory=list)  # hashtags that replace the automatic topic tags (e.g. a celebrity)
    photo_credit: str = ""  # set at publish time when a licensed photo is used; written into every caption

    def packed_flags(self) -> list[str]:
        """Flags plus category and tags, so they survive in the drafts table without new columns."""
        extra = [f"cat:{self.category}"] + [f"tag:{t}" for t in self.tags]
        return [f for f in self.flags if not f.startswith(("cat:", "tag:"))] + extra

    @classmethod
    def from_row(cls, row: dict) -> "Draft":
        flags = list(row.get("flags") or [])
        cat = next((f[4:] for f in flags if f.startswith("cat:")), "general")
        tags = [f[4:] for f in flags if f.startswith("tag:")]
        return cls(headline=row["headline"], body=row["body"], lane=Lane(row["lane"]), category=cat,
                   source_urls=row.get("sources") or [], label=row.get("label") or "",
                   flags=[f for f in flags if not f.startswith(("cat:", "tag:"))], tags=tags)
