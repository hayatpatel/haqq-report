"""Fetch feeds, dedupe, cap to the last 48 hours (plan section 6, steps 1 and 2)."""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .config import load_config
from .models import Article, Tier
from .sources import Source


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _entry_time(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


def _related(html: str) -> str:
    """Google News lists related coverage as <a>headline</a> <font>Publisher</font>; keep that as plain sentences."""
    pairs = re.findall(r"<a\b[^>]*>(.*?)</a>.*?<font[^>]*>(.*?)</font>", html or "", flags=re.S)
    return " ".join(f"{_strip_html(t)} ({_strip_html(p)})." for t, p in pairs[:6])


def parse_feed(source: Source, parsed_feed, cfg: dict | None = None) -> list[Article]:
    """Turn a feedparser result into Articles tagged with the source's tier."""
    articles = []
    for entry in parsed_feed.get("entries", []):
        url = entry.get("link", "").strip()
        title = _strip_html(entry.get("title", ""))
        if not url or not title:
            continue
        name, tier, summary = source.name, source.tier, _strip_html(entry.get("summary", ""))
        if source.aggregator:  # a news search feed: tier the REAL publisher, ignore unknown ones
            publisher = ((entry.get("source") or {}).get("title") or "").strip()
            trusted = (cfg or load_config()).get("trusted_publishers", {})
            if publisher not in trusted:
                continue
            name, tier = publisher, Tier(trusted[publisher])
            title = re.sub(rf"\s+-\s+{re.escape(publisher)}$", "", title)
            summary = _related(entry.get("summary", "")) or title
        articles.append(Article(
            url=url, title=title, summary=summary,
            source_name=name, tier=tier, published=_entry_time(entry),
            language=source.language, state_funded=source.state_funded,
        ))
    return articles


def fetch_source(source: Source) -> list[Article]:
    import socket

    import feedparser  # lazy import keeps tests light

    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(25)  # a hung feed must not freeze the listener
    try:
        return parse_feed(source, feedparser.parse(source.feed))
    finally:
        socket.setdefaulttimeout(old)


def normalise_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def dedupe(articles: Iterable[Article], seen_keys: set[str] | None = None) -> list[Article]:
    """Drop duplicates by URL and by near-identical title."""
    seen = set(seen_keys or ())
    seen_titles: set[str] = set()
    out: list[Article] = []
    for a in articles:
        title_key = normalise_title(a.title)
        if a.key in seen or title_key in seen_titles:
            continue
        seen.add(a.key)
        seen_titles.add(title_key)
        out.append(a)
    return out


def within_window(articles: Iterable[Article], now: datetime | None = None, cfg: dict | None = None) -> list[Article]:
    cfg = cfg or load_config()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=cfg["limits"]["INGEST_MAX_AGE_HOURS"])
    # Undated items are dropped: we cannot prove they are under 48h old, and after the 72h database cleanup
    # they would come back as "new" again and be posted twice.
    keep_undated = cfg.get("ingest", {}).get("keep_undated", False)
    return [a for a in articles if (a.published is None and keep_undated) or (a.published and a.published >= cutoff)]
