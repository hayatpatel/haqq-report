"""Source registry: feeds, tiers, creators (plan section 4)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT
from .models import Tier

SEED_PATH = ROOT / "data" / "sources.seed.json"


@dataclass(frozen=True)
class Source:
    name: str
    tier: Tier
    language: str = "en"
    country: str = ""
    feed: str = ""
    funding: str = ""
    bias_note: str = ""
    state_funded: bool = False
    priority: bool = False  # Palestine and priority feeds polled every 15 minutes

    @property
    def has_feed(self) -> bool:
        return bool(self.feed)


def load_sources(path: Path = SEED_PATH) -> list[Source]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for s in data["sources"]:
        out.append(Source(
            name=s["name"], tier=Tier(s["tier"]), language=s.get("language", "en"),
            country=s.get("country", ""), feed=s.get("feed", ""), funding=s.get("funding", ""),
            bias_note=s.get("bias_note", ""), state_funded=bool(s.get("state_funded", False)),
            priority=bool(s.get("priority", False)),
        ))
    return out


def feeds(only_priority: bool = False, path: Path = SEED_PATH) -> list[Source]:
    return [s for s in load_sources(path) if s.has_feed and (s.priority or not only_priority)]


def by_name(name: str, path: Path = SEED_PATH) -> Source | None:
    return next((s for s in load_sources(path) if s.name.lower() == name.lower()), None)


def attribution_phrase(source: Source) -> str:
    """Mandatory named attribution; state-funded outlets are labelled honestly."""
    if source.state_funded:
        return f"{source.name} (state-funded)"
    return source.name
