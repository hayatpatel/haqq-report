"""Celebrity coverage with one post per person per day.

Scope (a deliberate editorial choice): a watchlist of public figures tied to the mission, public-interest
events only, trusted outlets only. No gossip, relationships, health rumours (safety.GOSSIP_RE blocks them).
Rule: the first qualifying event of the day gets ONE post that combines everything reported that day.
A second post (UPDATE) is allowed only for critical news, at most `max_updates_per_day` times.
The per-day lock lives in the Supabase settings table (key celeb:<IST date>:<slug>), so two jobs can never
both post the same celebrity.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import load_config
from .models import Article
from .safety import GOSSIP_RE

CRITICAL_EVENT = re.compile(
    r"\b(dies|died|death|killed|passes away|arrested|detained|charged|sentenced|convicted|hospitali[sz]ed|"
    r"wins? (the )?(ballon d'or|world cup|champions league|premier league|title|trophy|oscar|grammy|emmy|bafta|golden)|"
    r"retire\w*|announces? (retirement|comeback)|sacked|banned|suspended)\b", re.IGNORECASE)
NOTABLE_EVENT = re.compile(
    r"\b(scores?|hat-?trick|goal|signs?|signed|transfer|joins?|awarded|honou?red|wins?|nominat\w*|"
    r"releases?|album|film|series|tour|donat\w*|charity|foundation|speaks? out|condemns?|calls? for|"
    r"gaza|palestin\w*|ceasefire|islamophob\w*|racis\w*|racist|abuse|hijab|ramadan|eid|hajj|umrah|launch\w*|record)\b",
    re.IGNORECASE)


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def hashtag(name: str) -> str:
    return "#" + re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode())


def watchlist(cfg: dict, store=None) -> list[dict]:
    """Config list, replaced by the list edited in Telegram (settings key `celebrities`) when present."""
    names = list(cfg.get("celebrity", {}).get("names", []))
    if store is not None:
        try:
            raw = store.get_setting("celebrities", "")
            if raw:
                names = json.loads(raw)
        except Exception:  # noqa: BLE001
            pass
    return names


def match(article: Article, watch: list[dict]) -> str | None:
    """Canonical name when the HEADLINE mentions a watched person (passing mentions in the body do not count)."""
    title = article.title
    for person in watch:
        for n in [person["name"], *person.get("aliases", [])]:
            if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", title, re.IGNORECASE):
                return person["name"]
    return None


def event_level(text: str) -> int:
    """0 = ignore (gossip or nothing happened), 2 = notable, 3 = critical."""
    if GOSSIP_RE.search(text):
        return 0
    if CRITICAL_EVENT.search(text):
        return 3
    return 2 if NOTABLE_EVENT.search(text) else 0


def day_key(name: str, cfg: dict, now: datetime | None = None) -> str:
    tz = ZoneInfo(cfg.get("celebrity", {}).get("timezone", "Asia/Kolkata"))
    return f"celeb:{(now or datetime.now(tz)).astimezone(tz):%Y-%m-%d}:{slug(name)}"


def lock(store, name: str, level: int, cfg: dict, now: datetime | None = None) -> str | None:
    """Reserve today's slot. Returns "first", "update", or None (already covered today). Atomic."""
    if store is None:
        return "first"
    key = day_key(name, cfg, now)
    current = store.get_setting(key, "")
    if not current:
        return "first" if store.try_insert("settings", {"key": key, "value": json.dumps({"posts": 1, "h": ""})}) else None
    data = json.loads(current)
    if level >= 3 and data.get("posts", 1) <= cfg.get("celebrity", {}).get("max_updates_per_day", 1):
        new = json.dumps({**data, "posts": data.get("posts", 1) + 1})
        return "update" if store.cas_setting(key, current, new) else None
    return None


def release(store, name: str, kind: str, cfg: dict, now: datetime | None = None) -> None:
    """Give the slot back when drafting failed, so the story is not lost for the day."""
    if store is None:
        return
    key = day_key(name, cfg, now)
    try:
        if kind == "first":
            store.delete("settings", {"key": f"eq.{key}"})
        else:
            cur = store.get_setting(key, "")
            data = json.loads(cur)
            store.cas_setting(key, cur, json.dumps({**data, "posts": max(1, data.get("posts", 2) - 1)}))
    except Exception:  # noqa: BLE001
        pass


def remember(store, name: str, headline: str, cfg: dict) -> None:
    """Keep today's headline so an UPDATE can say what was reported earlier."""
    if store is None:
        return
    try:
        key = day_key(name, cfg)
        data = json.loads(store.get_setting(key, "{}") or "{}")
        data["h"] = data.get("h") or headline
        store.set_setting(key, json.dumps(data))
    except Exception:  # noqa: BLE001
        pass


def earlier_headline(store, name: str, cfg: dict) -> str:
    try:
        return json.loads(store.get_setting(day_key(name, cfg), "{}") or "{}").get("h", "")
    except Exception:  # noqa: BLE001
        return ""


def combine(main: Article, others: list[Article], name: str, kind: str, earlier: str = "") -> Article:
    """One article carrying everything reported today about this person, each item with its outlet."""
    parts = []
    if kind == "update" and earlier:
        parts.append(f"Earlier today we reported: {earlier}. NEW DEVELOPMENT:")
    parts.append(f"{main.source_name} reports: {main.title}. {main.summary}".strip())
    for o in others[:4]:
        parts.append(f"{o.source_name} reports: {o.title}. {o.summary}".strip())
    return Article(url=main.url, title=main.title, summary=" ".join(parts)[:4000], source_name=main.source_name,
                   tier=main.tier, published=main.published, language=main.language,
                   state_funded=main.state_funded, related_urls=[o.url for o in others[:2]])


RULES = """- This is a public figure. Report only what a named, reputable outlet reports about their public life, work, achievements or public statements.
- No gossip, rumours, relationships, private life or health speculation. No guilt labels before a court.
- Combine every item below into ONE post with as much verified detail as fits, naming each outlet.
- If the text says "NEW DEVELOPMENT", start with the update and keep the earlier fact to one short clause."""
