"""Branded image card for every post (headline, topic, date; the source is in the caption): our own design, no third-party photos (copyright-safe).

render() makes a 1080x1080 JPEG. publish_url() uploads it to a public Supabase Storage bucket ("cards") and
returns the public URL for Facebook, Threads and Instagram. Never raises: no card means a text-only post.
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

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
        data = render(draft.headline, template.topic_label(text, cfg),
                      cfg.get("brand", {}).get("card_name", "HAQQ REPORT"), cfg.get("brand", {}).get("footer", ""))
        name = f"{datetime.now(timezone.utc):%Y-%m-%d}/{hashlib.sha1(draft.headline.encode()).hexdigest()[:12]}.jpg"
        return upload(data, name)
    except Exception as exc:  # noqa: BLE001 - a missing card must never block a post
        log.warning("card not made, posting text only: %s", redact(str(exc))[:200])
        return ""
