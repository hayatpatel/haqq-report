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
