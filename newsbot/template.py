"""The standard Haqq Report post template, fitted to each platform's length limit.

    HEADLINE

    Body (trimmed at a sentence or word boundary to fit)

    Source: <outlet>
    <link, on platforms that show links in the caption>

    #Topic #HaqqReport
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .config import load_config
from .models import Draft

LINK_IN_CAPTION = {"threads", "bluesky", "telegram_channel"}  # Facebook and Instagram keep links out of the caption


def source_name(url: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, name in cfg.get("source_names", {}).items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def topic_keys(text: str, cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    lower = text.lower()
    return [k for k in cfg.get("hashtags", {}) if re.search(rf"\b{re.escape(k)}\b", lower)]


def topic_label(text: str, cfg: dict | None = None) -> str:
    keys = topic_keys(text, cfg)
    return keys[0].upper() if keys else "WORLD"


def fit(text: str, n: int) -> str:
    """Trim to n characters at a sentence end if possible, else a word boundary, with an ellipsis."""
    text = text.strip()
    if len(text) <= n:
        return text
    if n <= 1:
        return ""
    cut = text[: n - 1]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if end >= n * 0.5:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


CATEGORY_TAGS = {"islamophobia": "#Islamophobia", "breaking": "#Breaking"}


def badge(draft: Draft, cfg: dict | None = None) -> str:
    """Short label for the image card."""
    if "update" in draft.flags:
        return "UPDATE"
    return {"breaking": "BREAKING", "celebrity": "SPOTLIGHT", "islamophobia": "ISLAMOPHOBIA"}.get(
        draft.category) or topic_label(f"{draft.headline} {draft.body}", cfg)


def kicker(draft: Draft) -> str:
    if "update" in draft.flags:
        return "UPDATE: "
    return "BREAKING: " if draft.category == "breaking" else ""


def build_caption(draft: Draft, platform: str, page_url: str = "", cfg: dict | None = None) -> str:
    """The platform's own post: hook, body (the platform rewrite when it passed checks), source, credit, hashtags.
    Always within the platform limit; the photo credit and source are kept, the body is what gets trimmed."""
    cfg = cfg or load_config()
    limit = cfg.get("caption_limits", {}).get(platform, 1800)
    style = cfg.get("platform_style", {}).get(platform, {})
    url = page_url or (draft.source_urls[0] if draft.source_urls else "")
    name = source_name(url, cfg) if url else ""
    if "google.com" in name and draft.source_label:  # aggregator link: name the real outlet
        name = draft.source_label

    tags_cfg = cfg.get("hashtags", {})
    brand_tag = cfg.get("brand", {}).get("hashtag", "#HaqqReport")
    topic_tags = [tags_cfg[k] for k in topic_keys(f"{draft.headline} {draft.body}", cfg)]
    lead = list(draft.tags) + ([CATEGORY_TAGS[draft.category]] if draft.category in CATEGORY_TAGS else [])
    lead = list(dict.fromkeys(lead + topic_tags))
    n_tags = style.get("hashtags", 3)
    tags = [lead[0] if lead else brand_tag] if n_tags <= 1 else lead[: n_tags - 1] + [brand_tag]

    lines = [f"Source: {name}"] if name else []
    link = url if (platform in LINK_IN_CAPTION and url and "news.google.com" not in url and len(url) <= 100) else ""
    if draft.photo_credit:
        lines.append(draft.photo_credit)
    foot = "\n\n".join(p for p in ["\n".join(lines), " ".join(dict.fromkeys(tags))] if p)
    cta = style.get("cta", "")

    head = kicker(draft) + fit(draft.headline, style.get("hook_chars", max(40, limit // 4)))
    body_src = draft.variants.get(platform) or draft.body
    extras = [x for x in (cta, link) if x]
    fixed = len(head) + len(foot) + sum(len(x) + 2 for x in extras) + 4
    if fixed > limit and link:  # no room for the link: the source name still names the outlet
        extras = [x for x in extras if x != link]
        fixed = len(head) + len(foot) + sum(len(x) + 2 for x in extras) + 4
    budget = limit - fixed
    body = fit(body_src, budget) if budget >= 60 else ""
    text = "\n\n".join(p for p in [head, body, cta, link if link in extras else "", foot] if p)
    if len(text) > limit:  # last resort: shorten the hook, never the credit or source
        head = fit(head, max(20, limit - len(foot) - 2))
        text = "\n\n".join(p for p in [head, foot] if p)
    return text[:limit]
