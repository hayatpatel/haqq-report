"""Story pipeline (plan section 6): ingest -> claims -> verify -> lane -> draft -> checks -> publish.

Nothing is published unless dry_run is False AND the platform is enabled in config.json AND the
credentials exist. In review mode every draft goes to Telegram for approval; Red always does.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from . import claims as claims_mod
from . import cards, islamophobia, safety, template, verify
from . import celebrity as celeb_mod
from .config import load_config, platform_enabled
from .models import Article, Draft, Lane, Tier
from .publish import bluesky, instagram, meta_fb, threads
from .publish import site as site_pub
from . import owner_feed
from .publish import telegram_channel
from .publish.base import PublishResult, run_platform

log = logging.getLogger("haqq.pipeline")

DRAFT_PROMPT = """Write a short, calm, factual news post for an independent news desk.
Rules:
- Name the source of every claim in the text ("UN OCHA said", "the Gaza Health Ministry said").
- Never say genocide, ethnic cleansing or war crime in your own voice; quote the named body that said it.
- No casualty figure without its named source in the same sentence.
- No collective blame, no inflammatory language, no praise of any armed group.
- No emoji. Max 600 characters. First line is a plain headline.
- Use only facts and numbers present in the text below.
{extra_rules}
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


def make_draft(article: Article, claims: list, label: str, ai_call: Callable[[str], str], *,
               category: str = "general", extra_rules: str = "", tags: list[str] | None = None,
               flags: list[str] | None = None) -> Draft:
    label_rule = f'- Begin the body with this label verbatim: "{label}"\n' if label else ""
    raw = ai_call(DRAFT_PROMPT.format(source=article.source_name, title=article.title,
                                      text=article.summary[:4000], label_rule=label_rule,
                                      extra_rules=extra_rules)).strip()
    headline, _, body = raw.partition("\n")
    headline = safety.clean_headline(headline)
    body = body.strip() or headline
    headline, body = safety.strip_emoji(headline), safety.strip_emoji(body)  # dignity rule: no emoji, ever
    return Draft(headline=headline, body=body, claims=claims, source_urls=[article.url, *article.related_urls],
                 label=label, category=category, tags=list(tags or []), flags=list(flags or []),
                 source_label=article.source_name)


def process_article(article: Article, ai_call: Callable[[str], str], cfg: dict | None = None, *,
                    category: str | None = None, extra_rules: str = "", tags: list[str] | None = None,
                    flags: list[str] | None = None) -> Outcome:
    """Run one article through claims, verification, drafting, lane and checks. Does NOT publish.

    A single Tier A or B source is enough (verify.evaluate_claim); that is also what the fast track relies on."""
    cfg = cfg or load_config()
    text_all = f"{article.title} {article.summary}"
    tags, flags = list(tags or []), list(flags or [])
    if islamophobia.is_islamophobia(text_all):
        if "#Islamophobia" not in tags:
            tags.append("#Islamophobia")
        if islamophobia.RULES not in extra_rules:
            extra_rules += islamophobia.RULES + "\n"
        category = category or "islamophobia"
    category = category or "general"
    if category == "breaking":
        extra_rules += "- This is breaking news: say clearly what is confirmed and by whom; do not speculate about causes or who is responsible.\n"
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
        draft = make_draft(article, found, label, ai_call, category=category, extra_rules=extra_rules,
                           tags=tags, flags=flags)
        result = safety.check_draft(draft, source_text, cfg)
        if result.ok:
            break
        failures = result.failures
        draft = None
    if draft is None:
        return Outcome(article.url, "skipped", "checks failed twice: " + "; ".join(failures))

    draft.lane, reasons = safety.classify_lane(draft, cfg)
    draft.flags = reasons

    needs_human = (draft.lane == Lane.RED and cfg.get("red_needs_approval", True)) or cfg.get("mode", "review") == "review"
    if needs_human:
        return Outcome(article.url, "needs_approval", ", ".join(reasons) or "review mode", draft)
    return Outcome(article.url, "ready", draft=draft)


def build_caption(draft: Draft, platform: str, page_url: str = "", cfg: dict | None = None) -> str:
    """Standard Haqq Report template, fitted to the platform's length limit (see template.py)."""
    return template.build_caption(draft, platform, page_url, cfg)


def publish_draft(draft: Draft, image_url: str = "", *, dry_run: bool = True,
                  alert: Callable[[str], None] | None = None, cfg: dict | None = None, store=None,
                  skip: set[str] | frozenset = frozenset(), story_only: bool = False) -> list[PublishResult]:
    """Publish to every enabled platform, each in isolation. Also posts the Facebook and Instagram stories.

    skip: platforms already done for this draft (never post twice). story_only: the feed post is blocked (daily cap),
    so only the stories go out."""
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

    wording = None
    if not dry_run and not draft.variants:
        from . import ai, variants
        src = template.source_name(draft.source_urls[0], cfg) if draft.source_urls else ""
        draft.variants = variants.adapt(draft, ai.generate, cfg, src)
        wording = ", ".join(sorted(draft.variants)) or "none"
    media = cards.Media(feed_url=image_url)
    if not dry_run and not image_url and any(platform_enabled(p, cfg) for p in
                                             ("facebook", "instagram", "threads", "facebook_story", "instagram_story")):
        media = cards.build(draft, cfg, store)
        image_url = media.feed_url
        if wording is not None:
            media.notes.append("wording tailored for: " + wording + ("" if wording != "none" else " (standard text used)"))
        draft.photo_credit = media.credit if image_url else ""  # credit only when the photo is really in the post

    def fb() -> str:
        caption = build_caption(draft, "facebook", cfg=cfg)
        post_id = meta_fb.post_photo(image_url, caption) if image_url else meta_fb.post_text(caption)
        # From here the post EXISTS. Nothing below may raise, or the retry would post it a second time.
        if draft.source_urls:
            try:
                meta_fb.first_comment(post_id, ("Sources:\n" + "\n".join(draft.source_urls[:3])) if len(draft.source_urls) > 1
                                      else "Source: " + draft.source_urls[0])
            except Exception as exc:  # noqa: BLE001 - usually a missing pages_manage_engagement permission
                log.warning("facebook source comment failed (%s); adding the link to the caption instead", str(exc)[:120])
                try:
                    meta_fb.update_message(post_id, caption + "\n" + "\n".join(draft.source_urls[:3]))
                except Exception as exc2:  # noqa: BLE001
                    log.warning("facebook caption update failed too: %s", str(exc2)[:120])
        return post_id

    jobs: dict[str, Callable[[], str]] = {
        "facebook": fb,
        "instagram": lambda: instagram.publish_image(image_url, build_caption(draft, "instagram", cfg=cfg)),
        "threads": lambda: threads.publish(build_caption(draft, "threads", page_url, cfg), image_url or None),
        "bluesky": lambda: bluesky.publish(build_caption(draft, "bluesky", page_url, cfg)),
        "telegram_channel": lambda: telegram_channel.send_to_channel(
            build_caption(draft, "telegram_channel", page_url, cfg), image_url or None),
    }

    jobs["facebook_story"] = lambda: meta_fb.post_story(media.story_url)
    jobs["instagram_story"] = lambda: instagram.publish_story(media.story_url)

    for platform, job in jobs.items():
        if not platform_enabled(platform, cfg) or platform in skip:
            continue
        is_story = platform.endswith("_story")
        if story_only and not is_story:
            continue
        if is_story and not dry_run and not media.story_url:
            results.append(PublishResult(platform, ok=False, skipped=True, error="story image not available"))
            continue
        if platform == "instagram" and not image_url:
            results.append(PublishResult(platform, ok=False, skipped=True, error="instagram needs an image"))
            continue
        if dry_run:
            results.append(PublishResult(platform, ok=True, skipped=True, extra={"dry_run": True}))
            continue
        res = run_platform(platform, job, alert=alert)
        if res.ok and platform in ("threads", "instagram"):
            try:  # a direct link so the owner can open (and delete) the post in one tap
                mod = threads if platform == "threads" else instagram
                res.extra["url"] = mod.permalink(res.post_id)
            except Exception:  # noqa: BLE001 - best effort
                pass
        results.append(res)
    owner_feed.post_results(draft.headline, results, dry_run=dry_run, cfg=cfg, lane=draft.lane.value, note=", ".join(f for f in draft.flags if f not in ("update",)),
                            tag=("FAST TRACK" if draft.category in ("breaking", "celebrity") else draft.category.upper() if draft.category != "general" else ""),
                            source=template.source_name(draft.source_urls[0], cfg) if draft.source_urls else "",
                            notes=media.notes)
    return results
