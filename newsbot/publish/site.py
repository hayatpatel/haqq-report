"""Static website generator: one sourced page per post plus a latest-posts hub.

Output goes to site/public/ (gitignored), deployed by Cloudflare Pages or GitHub Pages.
Every post gets a page so Instagram's non-clickable links have somewhere to land (plan fix #3).
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import ROOT

OUT_DIR = ROOT / "site" / "public"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | Haqq Report</title>
<meta name="description" content="{description}">
<style>
body{{font:17px/1.6 Georgia,serif;max-width:40rem;margin:2rem auto;padding:0 1rem;color:#1b1b1b}}
h1{{line-height:1.25}} .label{{background:#f4efe3;padding:.5rem .75rem;border-left:4px solid #8a6d1d}}
.meta{{color:#666;font-size:.9rem}} a{{color:#14532d}}
</style></head><body>
<p class="meta"><a href="/">Haqq Report</a> &middot; {date}</p>
<h1>{title}</h1>
{label}
{body}
<h2>Sources</h2>
<ul>{sources}</ul>
<p class="meta">Spotted an error? See the <a href="/corrections/">public corrections log</a>.</p>
</body></html>
"""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "post"


def render_post(title: str, body: str, source_urls: list[str], *, label: str = "",
                when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    paragraphs = "\n".join(f"<p>{html.escape(p)}</p>" for p in body.split("\n\n") if p.strip())
    sources = "".join(
        f'<li><a href="{html.escape(u, quote=True)}" rel="noopener nofollow">{html.escape(u)}</a></li>'
        for u in source_urls
    )
    return PAGE.format(
        title=html.escape(title),
        description=html.escape(body[:160].replace("\n", " ")),
        date=when.strftime("%d %b %Y"),
        label=f'<p class="label">{html.escape(label)}</p>' if label else "",
        body=paragraphs,
        sources=sources or "<li>None listed</li>",
    )


def write_post(title: str, body: str, source_urls: list[str], *, label: str = "",
               when: datetime | None = None, out_dir: Path = OUT_DIR) -> Path:
    when = when or datetime.now(timezone.utc)
    slug = f"{when:%Y-%m-%d}-{slugify(title)}"
    target = out_dir / "posts" / slug / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_post(title, body, source_urls, label=label, when=when), encoding="utf-8")
    return target


def publish(title: str, body: str, source_urls: list[str], label: str = "") -> str:
    """Pipeline entry point. Returns the relative URL of the generated page."""
    path = write_post(title, body, source_urls, label=label)
    return "/" + str(path.parent.relative_to(OUT_DIR)).replace("\\", "/") + "/"
