"""Islamic occasions via the free AlAdhan API (Umm al-Qura calendar).

Always add "may vary by one day with moon sighting" and show local dates when they differ.
Verify the AlAdhan endpoint parameters against https://aladhan.com/islamic-calendar-api before go-live.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import requests

MOON_NOTE = "Dates may vary by one day with moon sighting."

# (name, hijri_month, hijri_day, note)
OCCASIONS: list[tuple[str, int, int, str]] = [
    ("Islamic New Year", 1, 1, ""),
    ("Ashura", 1, 10, "Observed differently by Sunni and Shia communities; describe each as they describe themselves."),
    ("Mawlid", 3, 12, "Observance differs between communities; stay neutral."),
    ("Isra and Miraj", 7, 27, ""),
    ("Shab-e-Barat", 8, 15, "Observed mainly in South Asia."),
    ("Ramadan begins", 9, 1, "Daily series."),
    ("Laylat al-Qadr (traditionally the 27th night)", 9, 27, "Observed on odd nights of the last ten days by many."),
    ("Eid al-Fitr", 10, 1, ""),
    ("Day of Arafah", 12, 9, ""),
    ("Eid al-Adha", 12, 10, ""),
]

Fetcher = Callable[[str, dict], dict]


def _default_fetch(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def hijri_today(today: date, fetch: Fetcher = _default_fetch) -> tuple[int, int, int]:
    """Return (year, month, day) in the Hijri calendar for a Gregorian date."""
    data = fetch(f"https://api.aladhan.com/v1/gToH/{today:%d-%m-%Y}", {"calendarMethod": "UAQ"})
    h = data["data"]["hijri"]
    return int(h["year"]), int(h["month"]["number"]), int(h["day"])


def gregorian_for_hijri(year: int, month: int, day: int, fetch: Fetcher = _default_fetch) -> date:
    data = fetch(f"https://api.aladhan.com/v1/hToG/{day:02d}-{month:02d}-{year}", {"calendarMethod": "UAQ"})
    g = data["data"]["gregorian"]
    d, m, y = g["date"].split("-")
    return date(int(y), int(m), int(d))


def upcoming_occasions(today: date, days_ahead: int = 3, fetch: Fetcher = _default_fetch) -> list[dict]:
    """Occasions falling within the next `days_ahead` days (inclusive of today).

    The plan posts 3 days before, on the day, and a recap.
    """
    hy, _, _ = hijri_today(today, fetch)
    found: list[dict] = []
    for year in (hy, hy + 1):
        for name, month, day, note in OCCASIONS:
            greg = gregorian_for_hijri(year, month, day, fetch)
            delta = (greg - today).days
            if 0 <= delta <= days_ahead:
                found.append({"name": name, "gregorian": greg.isoformat(), "days_away": delta,
                              "hijri": f"{day} {month} {year}", "note": note, "moon_note": MOON_NOTE})
    return sorted(found, key=lambda o: o["days_away"])


def blackout_reminders(today: date, cfg: dict) -> list[str]:
    """Election-silence dates that are exactly N days away (reminder goes to Telegram)."""
    block = cfg.get("election_silence", {})
    lead = int(block.get("reminder_days_before", 14))
    out = []
    for item in block.get("blackouts", []):
        d = date.fromisoformat(item["date"])
        if (d - today).days == lead:
            out.append(f"Election silence for {item.get('country', 'unknown')} begins {d.isoformat()} ({lead} days away).")
    return out


def is_blackout(today: date, cfg: dict, country: str | None = None) -> bool:
    for item in cfg.get("election_silence", {}).get("blackouts", []):
        if country and item.get("country", "").lower() != country.lower():
            continue
        start = date.fromisoformat(item["date"])
        end = start + timedelta(days=int(item.get("days", 1)) - 1)
        if start <= today <= end:
            return True
    return False
