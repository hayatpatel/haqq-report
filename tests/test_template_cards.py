import io

import pytest

from newsbot import cards, config, pipeline, runner, template
from newsbot.models import Draft, Lane

CFG = config.load_config()
LONG = Draft(headline="H" * 200, body="Sentence one is here. " * 80, source_urls=["https://www.aljazeera.com/news/2026/10/1/x"])


@pytest.mark.parametrize("platform", ["facebook", "instagram", "threads", "bluesky", "telegram_channel"])
def test_caption_never_exceeds_platform_limit(platform):
    assert len(template.build_caption(LONG, platform, cfg=CFG)) <= CFG["caption_limits"][platform]


def test_threads_under_500_with_link_and_single_tag():
    d = Draft(headline="Gaza aid update", body="OCHA said x. " * 100, source_urls=["https://news.un.org/feed/view/en/story/2026/10/1166543"])
    text = template.build_caption(d, "threads", cfg=CFG)
    assert len(text) <= 500 and "Source: UN News" in text and "https://news.un.org" in text
    assert text.count("#") == 1


def test_facebook_keeps_link_out_of_caption_and_names_source():
    text = template.build_caption(LONG, "facebook", cfg=CFG)
    assert "Source: Al Jazeera" in text and "http" not in text and text.rstrip().endswith("#HaqqReport")


def test_card_is_a_1080_jpeg():
    from PIL import Image
    img = Image.open(io.BytesIO(cards.render("A very long headline " * 12, "GAZA", "UN News")))
    assert img.size == (1080, 1080) and img.format == "JPEG"


def test_missing_supabase_means_text_only_not_a_crash(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert cards.publish_url(Draft(headline="H", body="b"), CFG) == ""


def test_facebook_comment_failure_does_not_repost(monkeypatch):
    posts = []
    monkeypatch.setattr(pipeline.cards, "publish_url", lambda *a, **k: "")
    monkeypatch.setattr(pipeline.meta_fb, "post_text", lambda m: posts.append(m) or "1_2")
    monkeypatch.setattr(pipeline.meta_fb, "first_comment", lambda *a: (_ for _ in ()).throw(RuntimeError("403 #200")))
    monkeypatch.setattr(pipeline.meta_fb, "update_message", lambda *a: None)
    monkeypatch.setattr(pipeline.owner_feed, "post_results", lambda *a, **k: None)
    monkeypatch.setattr("newsbot.publish.base.JsonStateStore", lambda *a, **k: type("S", (), {
        "get": lambda s, p: {"failures": 0, "paused": False}, "set": lambda s, p, v: None})())
    cfg = {**CFG, "enable": {k: k == "facebook" for k in CFG["enable"]}}
    res = pipeline.publish_draft(Draft(headline="H", body="b", source_urls=["https://e.com/1"]), dry_run=False, cfg=cfg)
    assert len(posts) == 1 and res[0].ok and res[0].post_id == "1_2"


def test_unposted_green_drafts_get_one_final_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "publish_ready", lambda st, i, d, cfg, stats, final=False: calls.append((i, final)))

    class S:
        def select_raw(self, table, params):
            return [{"id": 5, "headline": "H", "body": "b", "lane": "green", "sources": [], "label": "", "flags": []}] \
                if table == "drafts" else []
    stats = {}
    runner.retry_unposted(S(), {"mode": "auto"}, stats)
    assert calls == [(5, True)] and stats["retried"] == 1
    calls.clear()
    runner.retry_unposted(S(), {"mode": "review"}, {})
    assert calls == []
