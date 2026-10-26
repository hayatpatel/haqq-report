"""Branded image cards: a 1080x1080 feed card and a 1080x1920 story card.

With a licensed Wikimedia Commons photo (photos.py) the photo is the darkened background; without one the card is plain brand green.
The photo credit is printed on stories (they have no caption) and written in every feed caption.
render() makes the plain 1080x1080 JPEG. publish_url() uploads it to a public Supabase Storage bucket ("cards") and
returns the public URL for Facebook, Threads and Instagram. Never raises: no card means a text-only post.
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dataclasses import dataclass, field

from . import template
from .config import load_config, optional_env, redact
from .models import Draft
from .store import auth_headers

log = logging.getLogger("haqq.cards")
SIZE = 1080
BUCKET = "cards"
BG, GOLD, WHITE, MUTED = (11, 31, 23), (201, 162, 75), (245, 245, 240), (150, 170, 158)
SERIF = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"]
SANS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]


def _font(paths: list[str], size: int):
    from PIL import ImageFont

    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def render(headline: str, topic: str, brand: str = "HAQQ REPORT",
           footer: str = "Truth beats speed", when: datetime | None = None) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    m = 80
    d.rectangle([0, 0, SIZE, 14], fill=GOLD)
    d.text((m, 70), brand, font=_font(SANS, 38), fill=GOLD)
    badge_font = _font(SANS, 28)
    tw = d.textlength(topic, font=badge_font)
    d.rounded_rectangle([SIZE - m - tw - 40, 62, SIZE - m, 112], radius=25, fill=GOLD)
    d.text((SIZE - m - tw - 20, 70), topic, font=badge_font, fill=BG)
    d.line([m, 150, SIZE - m, 150], fill=(40, 70, 56), width=2)

    text, max_w, max_h = headline.strip(), SIZE - 2 * m, 600
    for size in range(78, 35, -4):
        font = _font(SERIF, size)
        lines = _wrap(d, text, font, max_w)
        if len(lines) * int(size * 1.28) <= max_h:
            break
    else:
        lines = _wrap(d, template.fit(text, 120), font, max_w)
    y = 210
    for ln in lines:
        d.text((m, y), ln, font=font, fill=WHITE)
        y += int(size * 1.28)

    d.line([m, 900, SIZE - m, 900], fill=(40, 70, 56), width=2)
    stamp = (when or datetime.now(timezone.utc)).strftime("%d %b %Y").lstrip("0")
    small = _font(SANS, 28)
    d.text((m, 945), stamp, font=small, fill=WHITE)
    d.text((SIZE - m - d.textlength(footer, font=small), 945), footer, font=small, fill=MUTED)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def upload(data: bytes, name: str) -> str:
    """Upload to the public 'cards' bucket (creating it if needed). Returns the public URL or raises."""
    base, key = optional_env("SUPABASE_URL").rstrip("/"), optional_env("SUPABASE_KEY")
    if not (base and key):
        raise RuntimeError("Supabase not configured")
    headers = {**auth_headers(key), "Content-Type": "image/jpeg", "x-upsert": "true"}
    url = f"{base}/storage/v1/object/{BUCKET}/{name}"
    resp = requests.post(url, headers=headers, data=data, timeout=60)
    if resp.status_code in (400, 404) and "ucket" in resp.text:  # bucket missing: create it once, retry
        requests.post(f"{base}/storage/v1/bucket", headers=auth_headers(key),
                      json={"id": BUCKET, "name": BUCKET, "public": True}, timeout=30)
        resp = requests.post(url, headers=headers, data=data, timeout=60)
    if resp.status_code >= 300:
        raise RuntimeError(redact(f"storage upload HTTP {resp.status_code}: {resp.text[:200]}"))
    return f"{base}/storage/v1/object/public/{BUCKET}/{name}"


def publish_url(draft: Draft, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    if not cfg.get("cards", {}).get("enabled", True):
        return ""
    try:
        text = f"{draft.headline} {draft.body}"
        data = render(draft.headline, template.badge(draft, cfg),
                      cfg.get("brand", {}).get("card_name", "HAQQ REPORT"), cfg.get("brand", {}).get("footer", ""))
        name = f"{datetime.now(timezone.utc):%Y-%m-%d}/{hashlib.sha1(draft.headline.encode()).hexdigest()[:12]}.jpg"
        return upload(data, name)
    except Exception as exc:  # noqa: BLE001 - a missing card must never block a post
        log.warning("card not made, posting text only: %s", redact(str(exc))[:200])
        return ""


# ---------------------------------------------------------------- photo cards and stories
@dataclass
class Media:
    feed_url: str = ""
    story_url: str = ""
    credit: str = ""
    notes: list[str] = field(default_factory=list)  # short facts for the Telegram message (what worked, what failed)


def _cover(photo: bytes, w: int, h: int):
    """Photo scaled and centre-cropped to w x h, darkened so white text stays readable."""
    from PIL import Image, ImageEnhance

    img = Image.open(io.BytesIO(photo)).convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))
    img = ImageEnhance.Brightness(img).enhance(0.42)
    return img


def story_kicker(draft: Draft) -> str:
    if "update" in draft.flags:
        return "UPDATE"
    return {"breaking": "BREAKING", "celebrity": "SPOTLIGHT"}.get(draft.category, "DEVELOPING STORY")


def render_layout(draft: Draft, cfg: dict, size: tuple[int, int], photo: bytes | None = None,
                  credit: str = "") -> bytes:
    """Photo (or brand colour) background with brand + date header, topic badge, kicker, headline and footer."""
    from PIL import Image, ImageDraw

    w, h = size
    img = _cover(photo, w, h) if photo else Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    brand = cfg.get("brand", {})
    m, tall = 80, h > w
    top = 120 if tall else 70
    d.rectangle([0, 0, w, 14], fill=GOLD)
    d.text((m, top), brand.get("card_name", "HAQQ REPORT"), font=_font(SANS, 40), fill=GOLD)
    d.text((m, top + 52), f"{datetime.now(timezone.utc):%d %b %Y}".lstrip("0"), font=_font(SANS, 26), fill=WHITE)
    topic, bf = template.badge(draft, cfg), _font(SANS, 28)
    tw = d.textlength(topic, font=bf)
    d.rounded_rectangle([w - m - tw - 40, top - 8, w - m, top + 42], radius=25, fill=GOLD)
    d.text((w - m - tw - 20, top), topic, font=bf, fill=BG)

    text, max_w = draft.headline.strip(), w - 2 * m
    max_h = 760 if tall else 480
    for size_pt in range(84, 35, -4):
        font = _font(SERIF, size_pt)
        lines = _wrap(d, text, font, max_w)
        if len(lines) * int(size_pt * 1.28) <= max_h:
            break
    else:
        lines = _wrap(d, template.fit(text, 120), font, max_w)
    block = len(lines) * int(size_pt * 1.28)
    kf = _font(SANS, 30)
    y = (h - block) // 2 + (20 if tall else 60)
    k = story_kicker(draft)
    if k != topic:  # the badge already says it
        d.rounded_rectangle([m, y - 72, m + d.textlength(k, font=kf) + 40, y - 20], radius=8, fill=GOLD if k != "DEVELOPING STORY" else (170, 40, 40))
        d.text((m + 20, y - 66), k, font=kf, fill=BG if k != "DEVELOPING STORY" else WHITE)
    for ln in lines:
        d.text((m, y), ln, font=font, fill=WHITE)
        y += int(size_pt * 1.28)

    small = _font(SANS, 26)
    foot_y = h - (150 if tall else 100)
    d.line([m, foot_y - 20, w - m, foot_y - 20], fill=(90, 110, 100), width=2)
    d.text((m, foot_y), brand.get("hashtag", "#HaqqReport"), font=small, fill=GOLD)
    footer = brand.get("footer", "")
    d.text((w - m - d.textlength(footer, font=small), foot_y), footer, font=small, fill=MUTED)
    if credit and tall:  # stories have no caption, so the photo credit is printed on the image
        cf = _font(SANS, 22)
        d.text((m, foot_y + 50), template.fit(credit, 86), font=cf, fill=(200, 205, 200))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def build(draft: Draft, cfg: dict | None = None, store=None) -> Media:
    """Find a photo, render the feed card and the story card, upload both. Never raises; says what happened in notes."""
    from . import photos

    cfg = cfg or load_config()
    media = Media()
    if not cfg.get("cards", {}).get("enabled", True):
        return media
    photo = photos.pick(draft.headline, draft.body, draft.flags, draft.category, cfg, store)
    if photo:
        media.credit = photo.credit()
        media.notes.append(f"photo: {photo.title.replace('File:', '')[:50]} ({photo.license})")
    else:
        media.notes.append("photo: none found, brand card used")
    stem = f"{datetime.now(timezone.utc):%Y-%m-%d}/{hashlib.sha1(draft.headline.encode()).hexdigest()[:12]}"
    for kind, size, suffix in (("feed", (SIZE, SIZE), ""), ("story", (SIZE, 1920), "-s")):
        if kind == "story" and not (cfg.get("enable", {}).get("facebook_story") or cfg.get("enable", {}).get("instagram_story")):
            continue
        try:
            data = render_layout(draft, cfg, size, photo.data if photo else None, media.credit)
            url = upload(data, f"{stem}{suffix}.jpg")
            setattr(media, f"{kind}_url", url)
        except Exception as exc:  # noqa: BLE001 - a missing card must never block a post
            err = redact(str(exc))[:160]
            log.warning("%s card not made: %s", kind, err)
            media.notes.append(f"{kind} card failed: {err}")
            if kind == "feed" and photo:
                media.feed_url = photo.file_url  # licensed photo straight from Commons, no overlay
                media.notes.append("feed uses the Commons photo directly")
    return media
