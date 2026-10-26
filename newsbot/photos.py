"""Find a licensed, representative photo for a post (Wikimedia Commons only).

Legal rules enforced in code:
  * only Public domain, CC0 and CC BY (any version). No share-alike, non-commercial or no-derivatives licences,
    because the card overlays text on the photo (an adaptation) and CC BY allows that with credit;
  * a named author is required (except for public domain), shown in the caption as
    "Representative photo: <author> / Wikimedia Commons, <licence>";
  * files with a restriction note (personality, trademark, non-free) are refused;
  * no photos of children, bodies, wounded people, funerals or portraits of private people.
The photo is always called "representative": it shows the place, building or person named in the story, not
necessarily the event itself. If nothing safe and relevant is found, the post goes out without a photo.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass

import requests

from .config import load_config

log = logging.getLogger("haqq.photos")
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "HaqqReportBot/1.0 (https://github.com/hayatpatel/haqq-report; independent news desk)"}

BAD = re.compile(r"\b(dead|body|bodies|corpse\w*|victims?|wound\w*|injur\w*|blood\w*|child\w*|boys?|girls?|babies|baby|"
                 r"kids?|infant\w*|funeral\w*|coffin\w*|execut\w*|torture\w*|massacre\w*|mass grave|nude|naked|"
                 r"selfie|gore|graphic|prisoner\w*|detainee\w*|hostage\w*|logo)\b", re.IGNORECASE)
INSTITUTIONS = {
    r"\b(international court of justice|ICJ)\b": "Peace Palace The Hague",
    r"\b(international criminal court|ICC)\b": "International Criminal Court building The Hague",
    r"\bsecurity council\b": "United Nations Security Council chamber",
    r"\bgeneral assembly\b": "United Nations General Assembly Hall",
    r"\bUNRWA\b": "UNRWA",
    r"\b(world health organi[sz]ation|WHO)\b": "World Health Organization headquarters Geneva",
    r"\bUNICEF\b": "UNICEF",
    r"\b(world food programme|WFP)\b": "World Food Programme",
    r"\bKnesset\b": "Knesset building",
    r"\bWhite House\b": "White House",
    r"\bDowning Street\b": "10 Downing Street",
}
PLACES = ["Al-Aqsa Mosque", "Dome of the Rock", "Old City of Jerusalem", "Al-Shifa Hospital", "Khan Younis", "Gaza City",
          "Rafah", "Jabalia", "Deir al-Balah", "Gaza Strip", "Gaza", "West Bank", "Ramallah", "Hebron", "Nablus", "Jenin",
          "Bethlehem", "Jericho", "Jerusalem", "Khartoum", "Omdurman", "El Fasher", "Darfur", "Port Sudan", "Sudan",
          "Sanaa", "Aden", "Hodeidah", "Yemen", "Damascus", "Aleppo", "Idlib", "Homs", "Syria", "Beirut", "Lebanon",
          "Kabul", "Kandahar", "Afghanistan", "Mogadishu", "Somalia", "Cox's Bazar", "Rakhine", "Srinagar", "Kashmir",
          "Xinjiang", "Urumqi", "Kashgar", "Tripoli", "Benghazi", "Libya", "Tehran", "Isfahan", "Iran", "Baghdad", "Mosul",
          "Basra", "Iraq", "Cairo", "Istanbul", "Ankara", "Islamabad", "Karachi", "Lahore", "Peshawar", "Dhaka",
          "Jakarta", "Kuala Lumpur", "Mecca", "Medina", "Riyadh", "Jeddah", "Doha", "Amman", "Tunis", "Rabat", "Algiers",
          "Casablanca", "Niamey", "Bamako", "Djibouti", "Asmara", "Addis Ababa", "London", "Paris", "Berlin", "Brussels",
          "New York", "Washington", "New Delhi", "Delhi", "Mumbai", "Palestine"]


@dataclass
class Photo:
    file_url: str
    page_url: str
    title: str
    artist: str
    license: str
    query: str
    data: bytes = b""

    def credit(self) -> str:
        who = self.artist or "Unknown author"
        return f"Representative photo: {who} / Wikimedia Commons, {self.license}"


def person_of(flags: list[str]) -> str:
    return next((f[7:] for f in flags if f.startswith("person:")), "")


def queries(headline: str, body: str, flags: list[str], category: str = "general") -> list[tuple[str, str]]:
    """(search text, word that must appear in the file title or description), best first. At most 3."""
    text = f"{headline} {body}"
    out: list[tuple[str, str]] = []
    person = person_of(flags)
    if person:
        out.append((person, person.split()[-1].lower()))
    for pat, q in INSTITUTIONS.items():
        if re.search(pat, text):
            out.append((q, q.split()[0].lower() if q.split()[0].lower() not in ("united", "world", "international") else q.split()[1].lower()))
            break
    for place in PLACES:
        if re.search(rf"(?<!\w){re.escape(place)}(?!\w)", text, re.IGNORECASE):
            out.append((place, place.split()[0].lower().strip("'")))
            if len([o for o in out if o[0] in PLACES]) >= 2:
                break
    if category == "islamophobia" or re.search(r"\bmosque\b", text, re.IGNORECASE):
        out.append(("mosque exterior", "mosque"))
    seen, res = set(), []
    for q in out:
        if q[0] not in seen:
            seen.add(q[0]); res.append(q)
    return res[:3]


def license_ok(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    if n.startswith("cc0") or "public domain" in n or n.startswith("pd") or n == "pdm":
        return True
    return bool(re.fullmatch(r"cc[ -]by[ -]\d\.\d( [a-z]{2,3})?", n))  # CC BY 2.0, CC BY 4.0, CC BY 3.0 DE; never -SA, -NC, -ND


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def search(query: str, must: str, cfg: dict, used: set[str]) -> Photo | None:
    pc = cfg.get("photos", {})
    params = {"action": "query", "format": "json", "formatversion": 2, "generator": "search", "gsrnamespace": 6,
              "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": 20, "prop": "imageinfo",
              "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600,
              "iiextmetadatafilter": "LicenseShortName|Artist|ImageDescription|Restrictions|NonFree"}
    pages = requests.get(API, params=params, headers=UA, timeout=25).json().get("query", {}).get("pages", [])
    for p in sorted(pages, key=lambda x: x.get("index", 99)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        val = lambda k: _text((meta.get(k) or {}).get("value", ""))  # noqa: E731
        title, desc, lic, artist = p.get("title", ""), val("ImageDescription"), val("LicenseShortName"), val("Artist")
        if p.get("title") in used or not info.get("thumburl"):
            continue
        if not license_ok(lic) or val("Restrictions") or val("NonFree").lower() in ("true", "1"):
            continue
        if not artist and "public domain" not in lic.lower() and not lic.lower().startswith(("pd", "cc0")):
            continue
        if info.get("width", 0) < pc.get("min_width", 1200) or info.get("height", 0) < pc.get("min_height", 800):
            continue
        blob = f"{title} {desc}"
        if BAD.search(blob) or must not in blob.lower():
            continue
        return Photo(file_url=info["thumburl"], page_url=info.get("descriptionurl", ""), title=title,
                     artist=artist[:70], license=lic, query=query)
    return None


def pick(headline: str, body: str, flags: list[str], category: str = "general", cfg: dict | None = None,
         store=None) -> Photo | None:
    """A downloaded licensed photo, or None. Never raises."""
    cfg = cfg or load_config()
    if not cfg.get("photos", {}).get("enabled", True):
        return None
    used: set[str] = set()
    try:
        if store is not None:
            used = set(json.loads(store.get_setting("photos_used", "[]")))
    except Exception:  # noqa: BLE001
        pass
    try:
        for q, must in queries(headline, body, flags, category):
            photo = search(q, must, cfg, used)
            if not photo:
                continue
            resp = requests.get(photo.file_url, headers=UA, timeout=40)
            resp.raise_for_status()
            photo.data = resp.content
            if store is not None:
                try:
                    store.set_setting("photos_used", json.dumps(([photo.title] + list(used))[:40]))
                except Exception:  # noqa: BLE001
                    pass
            return photo
    except Exception as exc:  # noqa: BLE001 - no photo is fine
        log.warning("photo search failed (%s)", type(exc).__name__)
    return None
