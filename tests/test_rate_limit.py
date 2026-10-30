from datetime import datetime, timedelta, timezone

from newsbot import owner_feed, pipeline, ratelimit
from newsbot.config import load_config
from newsbot.models import Draft
from newsbot.publish import base

CFG = load_config()


def test_rate_limit_detection_is_specific():
    assert base.is_rate_limit('HTTP 400: {"code": 4, "message": "Application request limit reached"}')
    assert base.is_rate_limit('{"code": 17, "message": "User request limit reached"}')
    assert not base.is_rate_limit("Invalid image: Generate a new token")  # "rate" inside another word is not a rate limit
    assert not base.is_rate_limit('{"code": 190, "message": "expired"}')


def test_rate_limit_does_not_pause_the_platform():
    class Mem:
        d = {}
        def get(self, p): return self.d.get(p, {"failures": 0, "paused": False})
        def set(self, p, v): self.d[p] = v
    mem = Mem()
    def boom(): raise RuntimeError('HTTP 400: {"code": 4, "message": "Application request limit reached"}')
    for _ in range(5):
        r = base.run_platform("instagram", boom, store=mem, retries=0)
    assert r.error.startswith("rate limited") and not mem.d.get("instagram", {}).get("paused")
    what, _ = owner_feed.friendly_error(r.error)
    assert "limiting" in what


def test_cooldown_blocks_instagram_calls_and_expires(monkeypatch):
    class S:
        d = {}
        def get_setting(self, k, d=None): return self.d.get(k, d)
        def set_setting(self, k, v): self.d[k] = v
    s = S()
    ratelimit.start_cooldown(s, "instagram", CFG)
    assert ratelimit.cooldown_until(s, "instagram")
    assert ratelimit.cooldown_until(s, "instagram", datetime.now(timezone.utc) + timedelta(hours=2)) is None
    calls = []
    monkeypatch.setattr(pipeline.instagram, "publish_image", lambda *a: calls.append(1) or "1")
    monkeypatch.setattr(pipeline.instagram, "publish_story", lambda *a: calls.append(2) or "2")
    monkeypatch.setattr(pipeline.cards, "build", lambda *a, **k: pipeline.cards.Media(feed_url="u", story_url="s"))
    monkeypatch.setattr(pipeline.owner_feed, "post_results", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.variants if hasattr(pipeline, "variants") else __import__("newsbot.variants").variants, "adapt", lambda *a, **k: {})
    res = pipeline.publish_draft(Draft(headline="h", body="b"), dry_run=False, cfg=CFG, store=s,
                                 skip={"facebook", "facebook_story", "threads"})
    assert calls == [] and all("cooldown" in r.error for r in res)
