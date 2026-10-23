import pytest

from newsbot import config
from newsbot.publish import base, instagram, meta_fb, threads, tokens
from newsbot.publish.base import JsonStateStore, PublishError, run_platform


@pytest.fixture
def store(tmp_path):
    return JsonStateStore(tmp_path / "state.json")


def test_failure_on_one_platform_does_not_raise(store):
    def boom():
        raise PublishError("nope")

    res = run_platform("instagram", boom, store=store, sleep=lambda s: None)
    assert not res.ok and not res.skipped


def test_success_resets_failures(store):
    store.set("facebook", {"failures": 2, "paused": False})
    res = run_platform("facebook", lambda: "123", store=store, sleep=lambda s: None)
    assert res.ok and res.post_id == "123"
    assert store.get("facebook")["failures"] == 0


def test_platform_pauses_after_three_failures_and_only_that_platform(store):
    alerts = []

    def boom():
        raise PublishError("bad token")

    for _ in range(3):
        run_platform("instagram", boom, store=store, alert=alerts.append, sleep=lambda s: None)

    assert store.get("instagram")["paused"] is True
    assert store.get("facebook")["paused"] is False
    assert "paused" in alerts[-1]

    skipped = run_platform("instagram", lambda: "x", store=store, sleep=lambda s: None)
    assert skipped.skipped

    ok = run_platform("facebook", lambda: "y", store=store, sleep=lambda s: None)
    assert ok.ok


def test_retry_once_then_succeed(store):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise PublishError("transient")
        return "ok-id"

    res = run_platform("threads", flaky, store=store, sleep=lambda s: None)
    assert res.ok and calls["n"] == 2


def test_alert_message_never_contains_secret(store, monkeypatch):
    monkeypatch.setenv("IG_TOKEN", "SUPERSECRETTOKENVALUE123")
    alerts = []

    def boom():
        raise PublishError("HTTP 400 token SUPERSECRETTOKENVALUE123 invalid")

    run_platform("instagram", boom, store=store, alert=alerts.append, sleep=lambda s: None)
    assert alerts and "SUPERSECRETTOKENVALUE123" not in alerts[0]


def test_each_publisher_reads_only_its_own_credentials(monkeypatch):
    for name in ("FB_PAGE_ID", "FB_PAGE_TOKEN", "IG_USER_ID", "IG_TOKEN", "TH_USER_ID", "TH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("FB_PAGE_ID", "1")
    monkeypatch.setenv("FB_PAGE_TOKEN", "fbtoken-12345678")
    # Instagram and Threads creds are absent: Facebook creds must not satisfy them.
    with pytest.raises(config.MissingSecret) as ig_err:
        instagram._creds()
    assert "IG_USER_ID" in str(ig_err.value) and "fbtoken" not in str(ig_err.value)
    with pytest.raises(config.MissingSecret):
        threads._creds()
    assert meta_fb._creds() == ("1", "fbtoken-12345678")


def test_facebook_photo_post_calls_graph_with_page_token(monkeypatch):
    monkeypatch.setenv("FB_PAGE_ID", "42")
    monkeypatch.setenv("FB_PAGE_TOKEN", "tok-abcdefgh")
    seen = {}

    def fake(method, url, **kw):
        seen.update(method=method, url=url, data=kw.get("data"))
        return {"post_id": "42_99"}

    monkeypatch.setattr(meta_fb, "graph_request", fake)
    assert meta_fb.post_photo("https://x/y.jpg", "caption") == "42_99"
    assert seen["url"].endswith("/42/photos") and seen["data"]["access_token"] == "tok-abcdefgh"


def test_instagram_carousel_requires_2_to_10_images():
    with pytest.raises(PublishError):
        instagram.publish_carousel(["https://x/1.jpg"], "c")


def test_instagram_waits_for_container(monkeypatch):
    states = iter([{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}])
    monkeypatch.setattr(instagram, "graph_request", lambda *a, **k: next(states))
    instagram.wait_until_ready("c1", "tok", sleep=lambda s: None)


def test_instagram_container_error_raises(monkeypatch):
    monkeypatch.setattr(instagram, "graph_request", lambda *a, **k: {"status_code": "ERROR"})
    with pytest.raises(PublishError):
        instagram.wait_until_ready("c1", "tok", sleep=lambda s: None)


def test_token_age_rules():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    cfg = {"meta": {"token_refresh_after_days": 40, "token_alert_days_before_expiry": 10, "token_lifetime_days": 60}}
    assert not tokens.needs_refresh(now - timedelta(days=39), now, cfg)
    assert tokens.needs_refresh(now - timedelta(days=41), now, cfg)
    assert not tokens.should_alert(now - timedelta(days=45), now, cfg)
    assert tokens.should_alert(now - timedelta(days=51), now, cfg)


def test_graph_request_redacts_secrets_from_errors(monkeypatch):
    import requests

    monkeypatch.setenv("FB_PAGE_TOKEN", "REALTOKENVALUE9999")

    def fake_request(*a, **k):
        raise requests.ConnectionError("failed for url ...access_token=REALTOKENVALUE9999")

    monkeypatch.setattr(base.requests, "request", fake_request)
    with pytest.raises(PublishError) as err:
        base.graph_request("GET", "https://graph.facebook.com/x")
    assert "REALTOKENVALUE9999" not in str(err.value)
