import time

from newsbot import owner_feed, socialtoken
from newsbot.publish import base


class Mem:
    def __init__(self): self.d = {}
    def get(self, p): return self.d.get(p, {"failures": 0, "paused": False})
    def set(self, p, v): self.d[p] = v


def test_pause_shows_last_error_and_expires(monkeypatch):
    mem = Mem()
    def boom(): raise RuntimeError('HTTP 400: {"code": 190, "message": "expired"}')
    for _ in range(3):
        base.run_platform("instagram", boom, store=mem, retries=0)
    res = base.run_platform("instagram", boom, store=mem, retries=0)
    assert res.skipped and "Last error" in res.error and "190" in res.error
    what, fix = owner_feed.friendly_error(res.error)
    assert "expired or invalid" in what and "new token" in fix
    mem.d["instagram"]["paused_at"] = time.time() - 31 * 60  # pause over: tried again
    assert not base.run_platform("instagram", lambda: "42", store=mem, retries=0).skipped


def test_token_check_reports_exact_error(monkeypatch):
    monkeypatch.setenv("IG_TOKEN", "SECRETTOKEN")
    class R:
        status_code = 400; content = b"x"
        def json(self): return {"error": {"message": "Error validating access token: Session has expired", "code": 190}}
    monkeypatch.setattr(socialtoken.requests, "get", lambda *a, **k: R())
    out = socialtoken.check("instagram")
    assert "Session has expired" in out and "SECRETTOKEN" not in out and "/igtoken" in out


def test_instagram_page_route_switches_endpoint_and_token(monkeypatch):
    from newsbot.publish import instagram
    monkeypatch.setenv("IG_USER_ID", "1"); monkeypatch.setenv("IG_TOKEN", "login-token")
    monkeypatch.setenv("FB_PAGE_TOKEN", "page-token"); monkeypatch.setenv("IG_BUSINESS_ID", "999")
    monkeypatch.delenv("IG_ROUTE", raising=False)
    assert instagram._creds() == ("1", "login-token") and "graph.instagram.com" in instagram._base()
    monkeypatch.setenv("IG_ROUTE", "page")
    assert instagram._creds() == ("999", "page-token") and "graph.facebook.com" in instagram._base()


def test_refresh_due_saves_new_token_and_waits_20_days(monkeypatch):
    store = type("S", (), {"d": {}, "get_setting": lambda s, k, d=None: s.d.get(k, d),
                           "set_setting": lambda s, k, v: s.d.__setitem__(k, v),
                           "upsert": lambda s, t, row, c: s.d.__setitem__(row["key"], row["value"])})()
    monkeypatch.setenv("IG_TOKEN", "old"); monkeypatch.delenv("TH_TOKEN", raising=False)
    class R:
        status_code = 200; text = ""
        def json(self): return {"access_token": "new"}
    monkeypatch.setattr(socialtoken.requests, "get", lambda *a, **k: R())
    assert socialtoken.refresh_due(store) == ["instagram token refreshed"] and store.d["IG_TOKEN"] == "new"
    assert socialtoken.refresh_due(store) == []  # refreshed today: nothing to do


def test_catch_up_starts_overdue_pipeline_once():
    from datetime import datetime, timedelta, timezone
    from newsbot import approvals
    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    class S:
        d = {}
        def __init__(self, last): self.last = last
        def select_raw(self, t, p): return [{"started_at": self.last.isoformat()}]
        def get_setting(self, k, d=None): return self.d.get(k, d)
        def set_setting(self, k, v): self.d[k] = v
    calls = []
    fresh = S(now - timedelta(hours=2))
    assert approvals.catch_up(fresh, {}, now, lambda: calls.append(1)) is False
    late = S(now - timedelta(hours=9))
    assert approvals.catch_up(late, {}, now, lambda: calls.append(1)) is True
    assert approvals.catch_up(late, {}, now + timedelta(minutes=10), lambda: calls.append(1)) is False  # already requested
    assert len(calls) == 1


def test_daily_digest_builds_and_sends_once(monkeypatch):
    from datetime import datetime, timezone
    from newsbot import digest
    sent = []
    monkeypatch.setattr(digest.owner_feed, "message", lambda t: sent.append(t))
    class S:
        d = {}
        def select_raw(self, t, p):
            if t == "posts":
                return [{"platform": "facebook", "draft_id": 1, "drafts": {"headline": "Aid reaches Gaza"}},
                        {"platform": "facebook_story", "draft_id": 1, "drafts": {"headline": "Aid reaches Gaza"}}]
            if t == "run_logs": return [{"name": "run", "status": "ok"}]
            return [{"id": 5}]
        def get_setting(self, k, d=None): return self.d.get(k, d)
        def set_setting(self, k, v): self.d[k] = v
    s = S()
    morning = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)  # 08:30 IST
    assert digest.send_daily(s, {}, morning) and "Aid reaches Gaza" in sent[0] and "Full runs: 1 of 6" in sent[0]
    assert not digest.send_daily(s, {}, morning)  # once a day
    assert not digest.send_daily(S(), {}, datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc))  # before 08:00 IST


def test_listener_ends_itself_when_new_code_is_pushed(monkeypatch):
    from newsbot import approvals
    monkeypatch.setattr(approvals, "poll", lambda *a, **k: {"handled": 0, "errors": 0, "results": []})
    monkeypatch.setattr(approvals, "catch_up", lambda *a, **k: False)
    t = {"now": 0.0}
    def clock():
        t["now"] += 100
        return t["now"]
    restarted = []
    out = approvals.listen(None, 600, wait=0, cfg={"fast_track": {"enabled": False}}, sleep=lambda s: None, clock=clock,
                           updated=lambda: True, restart=lambda: restarted.append(1))
    assert out.get("restarted_for_new_code") and restarted == [1]
    t["now"] = 0.0
    out = approvals.listen(None, 1000, wait=0, cfg={"fast_track": {"enabled": False}}, sleep=lambda s: None, clock=clock,
                           updated=lambda: False, restart=lambda: restarted.append(2))
    assert not out.get("restarted_for_new_code") and restarted == [1]
