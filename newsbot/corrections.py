"""Public corrections log (mandatory, linked in every bio).

Append-only JSONL at data/corrections.jsonl, rendered to a public page.
Facebook posts can be retracted via the API; Instagram and Threads need manual removal,
so every entry records whether manual removal is still pending.
"""
from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

LOG_PATH = ROOT / "data" / "corrections.jsonl"
OUT_PATH = ROOT / "site" / "public" / "corrections" / "index.html"


@dataclass
class Correction:
    post_id: str
    what_changed: str
    date: str
    kind: str = "correction"  # correction | retraction | update
    manual_removal_pending: bool = False


def add_correction(post_id: str, what_changed: str, *, kind: str = "correction",
                   manual_removal_pending: bool = False, when: datetime | None = None,
                   path: Path = LOG_PATH) -> Correction:
    when = when or datetime.now(timezone.utc)
    entry = Correction(post_id, what_changed.strip(), when.date().isoformat(), kind, manual_removal_pending)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    return entry


def read_corrections(path: Path = LOG_PATH) -> list[Correction]:
    if not path.exists():
        return []
    return [Correction(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def corrections_per_100_posts(total_posts: int, path: Path = LOG_PATH) -> float:
    """KPI: target is under 1 per 100 posts."""
    if total_posts <= 0:
        return 0.0
    return round(len(read_corrections(path)) * 100 / total_posts, 2)


def render_html(path: Path = LOG_PATH) -> str:
    items = sorted(read_corrections(path), key=lambda c: c.date, reverse=True)
    rows = "".join(
        f"<li><strong>{html.escape(c.date)}</strong> &middot; {html.escape(c.kind)} &middot; "
        f"post {html.escape(c.post_id)}<br>{html.escape(c.what_changed)}</li>"
        for c in items
    ) or "<li>No corrections so far. Every future one will be listed here.</li>"
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Corrections | Haqq Report</title></head><body style=\"font:17px/1.6 Georgia,serif;"
        "max-width:40rem;margin:2rem auto;padding:0 1rem\">"
        "<h1>Corrections log</h1><p>Every correction, update and retraction, in public.</p>"
        f"<ul>{rows}</ul></body></html>"
    )


def write_html(path: Path = LOG_PATH, out: Path = OUT_PATH) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(path), encoding="utf-8")
    return out
