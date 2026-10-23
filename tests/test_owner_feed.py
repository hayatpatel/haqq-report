from newsbot import owner_feed
from newsbot.publish.base import PublishResult

ON = {"owner_feed": {"posts": True, "errors": True, "daily_health_report": True}}
OFF = {"owner_feed": {"posts": False, "errors": False, "daily_health_report": False}}


def capture(monkeypatch):
    sent = []
    monkeypatch.setattr(owner_feed, "alert_owner", sent.append)
    return sent


def test_dry_run_summary_is_labelled(monkeypatch):
    sent = capture(monkeypatch)
    owner_feed.post_results("UN OCHA reports aid", [PublishResult("facebook", ok=True, skipped=True)], dry_run=True, cfg=ON)
    assert len(sent) == 1 and "DRY RUN" in sent[0] and "facebook: would post" in sent[0]


def test_live_success_and_failure_reported(monkeypatch):
    sent = capture(monkeypatch)
    res = [PublishResult("facebook", ok=True, post_id="1_2"), PublishResult("instagram", ok=False, error="token expired")]
    owner_feed.post_results("Headline", res, dry_run=False, cfg=ON)
    assert "ATTENTION" in sent[0] and "facebook: OK 1_2" in sent[0] and "instagram: FAILED token expired" in sent[0]


def test_disabled_flags_send_nothing(monkeypatch):
    sent = capture(monkeypatch)
    owner_feed.post_results("H", [PublishResult("facebook", ok=True, post_id="1")], dry_run=False, cfg=OFF)
    owner_feed.error("ingest", "boom", cfg=OFF)
    owner_feed.health_report("all fine", failed=False, cfg=OFF)
    assert sent == []


def test_failed_health_always_sent_even_when_daily_report_off(monkeypatch):
    sent = capture(monkeypatch)
    owner_feed.health_report("instagram FAIL", failed=True, cfg=OFF)
    assert sent and sent[0].startswith("ACTION NEEDED")


def test_feed_never_raises(monkeypatch):
    def boom(_):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(owner_feed, "alert_owner", boom)
    owner_feed.error("x", "y", cfg=ON)
    owner_feed.message("hello")


def test_test_post_is_dry_run_by_default(monkeypatch, capsys):
    from newsbot import __main__ as cli
    monkeypatch.setattr(owner_feed, "alert_owner", lambda t: None)
    monkeypatch.setattr("newsbot.publish.meta_fb.post_text", lambda t: (_ for _ in ()).throw(AssertionError("must not post")))
    assert cli.main(["test-post", "facebook"]) == 0
    assert "would post" in capsys.readouterr().out


def test_test_post_live_facebook_posts_then_deletes(monkeypatch, capsys):
    from newsbot import __main__ as cli
    calls = []
    monkeypatch.setattr(owner_feed, "alert_owner", lambda t: None)
    monkeypatch.setattr("newsbot.publish.meta_fb.post_text", lambda t: calls.append("post") or "1_2")
    monkeypatch.setattr("newsbot.publish.meta_fb.delete_post", lambda i: calls.append("delete") or True)
    assert cli.main(["test-post", "facebook", "--live"]) == 0
    assert calls == ["post", "delete"] and "deleted again: True" in capsys.readouterr().out


def test_test_post_reports_failure_without_raising(monkeypatch, capsys):
    from newsbot import __main__ as cli
    monkeypatch.setattr(owner_feed, "alert_owner", lambda t: None)
    assert cli.main(["test-post", "instagram", "--live"]) == 1
    assert "needs --image-url" in capsys.readouterr().out
