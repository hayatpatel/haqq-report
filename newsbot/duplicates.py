"""Near-duplicate story check against recent drafts in the database (same event, different outlet or wording)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

STOP = {"the", "and", "for", "with", "that", "this", "from", "has", "have", "are", "was", "were", "its", "after",
        "over", "into", "amid", "says", "said", "new", "more", "than", "his", "her", "their", "will", "but"}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def similar(a: str, b: str, threshold: float = 0.55) -> bool:
    wa, wb = words(a), words(b)
    if wa and wb and len(wa & wb) / len(wa | wb) >= threshold:
        return True
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.8


def find_duplicate(headline: str, recent: list[str]) -> str | None:
    return next((h for h in recent if similar(headline, h)), None)


def recent_headlines(store, hours: int = 72) -> list[str]:
    """Headlines of drafts that were posted, approved, or are waiting for approval in the last `hours`."""
    if store is None:
        return []
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        rows = store.select_raw("drafts", {"select": "headline", "created_at": f"gte.{since}",
                                           "status": "in.(pending,approved,posted)"})
    except Exception:  # noqa: BLE001 - the check is best effort; the DB claim is the hard guarantee
        return []
    return [r["headline"] for r in rows if r.get("headline")]
