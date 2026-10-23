"""Story pipeline (plan section 6): ingest -> claims -> verify -> lane -> draft -> checks -> publish.

Nothing is published unless dry_run is False AND the platform is enabled in config.json AND the
credentials exist. In review mode every draft goes to Telegram for approval; Red always does.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from . import claims as claims_mod
from . import safety, verify
from .config import load_config, platform_enabled
from .models import Article, Draft, Lane, Tier
from .publish import bluesky, instagram, meta_fb, threads
from .publish import site as site_pub
from .publish import telegram_channel
from .publish.base import PublishResult, run_platform

log = logging.getLogger("haqq.pipeline")

DRAFT_PROMPT = """Write a short, calm, factual news post for an independent news desk.
Rules:
- Name the source of every claim in the text ("UN OCHA said", "the Gaza Health Ministry said").
- Never say genocide, ethnic cleansing or war crime in your own voice; quote the named body that said it.
- No casualty figure without its named source in the same sentence.
- No collective blame, no inflammatory language, no praise of any armed group.
- At most 2 emoji. Max 600 characters. First line is a plain headline.
- Use only facts and numbers present in the text below.

SOURCE: {source}
TITLE: {title}
TEXT: {text}
{label_rule}"""


@dataclass
class Outcome:
    article_url: str
    action: str  # skipped | held | needs_approval | ready
    reason: str = ""
    draft: Draft | None = None
    results: list[PublishResult] = field(default_factory=list)


def make_draft(article: Article, claims: list, label: str, ai_call: Callable[[str], str]) -> Draft:
    label_rule = f'- Begin the body with this label verbatim: "{label}"\n' if label else ""
    raw = ai_call(DRAFT_PROMPT.format(source=article.source_name, title=article.title,
                                      text=article.summary[:4000], label_rule=label_rule)).strip()
    headline, _, body = raw.partition("\n")
    return Draft(headline=headline.strip(), body=(body.strip() or headline.strip()), claims=claims,
                 source_urls=[article.url], label=label)


def process_article(article: Article, ai_call: Callable[[str], str], cfg: dict | None = None) -> Outcome:
    """Run one article through claims, verification, drafting, lane and checks. Does NOT publish."""
    cfg = cfg or load_config()
    min_tier = Tier(cfg["limits"]["VERIFY_MIN_TIER"])

    found = claims_mod.extract_claims(article, ai_call)
    if not found:
        return Outcome(article.url, "skipped", "no traceable claims")

    ok, verdicts = verify.evaluate_claims(found, min_tier=min_tier)
    if not ok:
        reasons = "; ".join(v.reason for v in verdicts if not v.publishable) or "not verified"
        return Outcome(article.url, "held", reasons)

    label = next((v.label for v in verdicts if v.label), "")
    source_text = f"{article.title} {article.summary}"

    draft = None
    failures: list[str] = []
    for _attempt in range(2):  # retry once, then skip
        draft = make_draft(article, found, label, ai_call)
        result = safety.check_draft(draft, source_text, cfg)
        if result.ok:
            break
        failures = result.failures
        draft = None
    if draft is None:
        return Outcome(article.url, "skipped", "checks failed twice: " + "; ".join(failures))

    draft.lane, reasons = safety.classify_lane(draft, cfg)
    draft.flags = reasons

    needs_human = draft.lane == Lane.RED or cfg.get("mode", "review") == "review"
    if needs_human:
        return Outcome(article.url, "needs_approval", ", ".join(reasons) or "review mode", draft)
    return Outcome(article.url, "ready", draft=draft)


def build_caption(draft: Draft, platform: str, page_url: str = "") -> str:
    """Per-platform caption. Full publisher URL only (no shorteners); Meta links go in a first comment
    or on the website page, never as a shortened link in the caption."""
    base = f"{draft.headline}\n\n{draft.body}".strip()
    if platform in ("threads", "bluesky", "telegram_channel") and (page_url or draft.source_urls):
        base += f"\n\n{page_url or draft.source_urls[0]}"
    return base


def publish_draft(draft: Draft, image_url: str = "", *, dry_run: bool = True,
                  alert: Callable[[str], None] | None = None, cfg: dict | None = None) -> list[PublishResult]:
    """Publish to every enabled platform, each in isolation."""
    cfg = cfg or load_config()
    results: list[PublishResult] = []

    page_url = ""
    if platform_enabled("website", cfg):
        if dry_run:
            results.append(PublishResult("website", ok=True, skipped=True, extra={"dry_run": True}))
        else:
            res = run_platform("website", lambda: site_pub.publish(draft.headline, draft.body, draft.source_urls, draft.label),
                               alert=alert)
            results.append(res)
            if res.ok:
                page_url = cfg["site"].get("base_url", "").rstrip("/") + res.post_id

    def fb() -> str:
        post_id = (meta_fb.post_photo(image_url, build_caption(draft, "facebook"))
                   if image_url else meta_fb.post_text(build_caption(draft, "facebook")))
        if draft.source_urls:
            meta_fb.first_comment(post_id, "Source: " + draft.source_urls[0])
        return post_id

    jobs: dict[str, Callable[[], str]] = {
        "facebook": fb,
        "instagram": lambda: instagram.publish_image(image_url, build_caption(draft, "instagram")),
        "threads": lambda: threads.publish(build_caption(draft, "threads", page_url), image_url or None),
        "bluesky": lambda: bluesky.publish(build_caption(draft, "bluesky", page_url)),
        "telegram_channel": lambda: telegram_channel.send_to_channel(
            build_caption(draft, "telegram_channel", page_url), image_url or None),
    }

    for platform, job in jobs.items():
        if not platform_enabled(platform, cfg):
            continue
        if platform == "instagram" and not image_url:
            results.append(PublishResult(platform, ok=False, skipped=True, error="instagram needs an image"))
            continue
        if dry_run:
            results.append(PublishResult(platform, ok=True, skipped=True, extra={"dry_run": True}))
            continue
        results.append(run_platform(platform, job, alert=alert))
    return results
