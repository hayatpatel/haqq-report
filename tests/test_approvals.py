from datetime import datetime, timedelta, timezone

import pytest

from newsbot import approvals, owner_feed, pipeline
from newsbot.publish.base import PublishResult

CFG = {"limits": {"MAX_POSTS_PER_DAY": 2, "INGEST_MAX_AGE_HOURS": 48}, "approvals": {"max_age_hours": 24}}


class Store:
    def __init__(self, status="pending", age_h=1, posted_today=0):
        self.draft = {"id": 7, "status": status, "headline": "H", "body": "OCHA said x.", "lane": "amber",
                      "sources": ["https://e.com/1"], "label": "", "flags": [],
                      "created_at": (datetime.now(timezone.utc) - timedelta(hours=age_h)).isoformat()}
        self.posted_today, self.updates, self.inserts = posted_today, [], []

    def select_raw(self, table, params):
        if table == "drafts" and "id" in params:
            return [self.draft]
        if table == "drafts":
            return [{"id": i} for i in range(self.posted_today)]
        return []

    def update(self, table, match, values):
        self.updates.append(values); self.draft.update(values)

    def claim(self, table, row_id, from_status, to_status, extra=None):
        if self.draft["status"] != from_status:
            return False
        self.draft.update({"status": to_status, **(extra or {})})
        return True

    def insert(self, table, row):
        self.inserts.append((table, row))


@pytest.fixture(autouse=True)
def fake_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
    monkeypatch.setattr(approvals, "_call", lambda *a, **k: {})
    monkeypatch.setattr(owner_feed, "message", lambda *a, **k: None)
    monkeypatch.setattr(owner_feed, "error", lambda *a, **k: None)


def tap(data, user=42):
    return {"id": "c1", "data": data, "from": {"id": user}, "message": {"chat": {"id": 42}, "message_id": 1}}


def test_approve_posts_and_records(monkeypatch):
    monkeypatch.setattr(pipeline, "publish_draft",
                        lambda *a, **k: [PublishResult("facebook", ok=True, post_id="p1")])
    st = Store()
    assert "posted: facebook" in approvals.handle(st, tap("a:7"), CFG)
    assert st.draft["status"] == "posted" and st.inserts == [("posts", {"draft_id": 7, "platform": "facebook", "platform_post_id": "p1"})]


def test_stranger_cannot_approve(monkeypatch):
    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: pytest.fail("must not publish"))
    st = Store()
    assert "not the owner" in approvals.handle(st, tap("a:7", user=99), CFG)
    assert st.draft["status"] == "pending"


def test_second_tap_does_nothing(monkeypatch):
    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: pytest.fail("must not publish twice"))
    assert "already posted" in approvals.handle(Store(status="posted"), tap("a:7"), CFG)


def test_old_draft_expires_and_limit_blocks(monkeypatch):
    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: pytest.fail("must not publish"))
    st = Store(age_h=30)
    assert "expired" in approvals.handle(st, tap("a:7"), CFG) and st.draft["status"] == "skipped"
    assert "daily limit" in approvals.handle(Store(posted_today=2), tap("a:7"), CFG)


def test_failed_publish_returns_to_pending(monkeypatch):
    monkeypatch.setattr(pipeline, "publish_draft",
                        lambda *a, **k: [PublishResult("facebook", ok=False, error="token expired")])
    st = Store()
    assert "NOT posted" in approvals.handle(st, tap("a:7"), CFG) and st.draft["status"] == "pending"


def test_reject():
    st = Store()
    assert "rejected" in approvals.handle(st, tap("r:7"), CFG) and st.draft["status"] == "rejected"


def test_auto_publish_records_post_and_respects_daily_cap(monkeypatch):
    from newsbot import runner
    from newsbot.models import Draft, Lane

    monkeypatch.setattr(pipeline, "publish_draft",
                        lambda *a, **k: [PublishResult("facebook", ok=True, post_id="p9")])
    d = Draft(headline="H", body="b", lane=Lane.GREEN)
    st, stats = Store(), {}
    runner.publish_ready(st, 7, d, CFG, stats)
    assert stats["published"] == 1 and st.draft["status"] == "posted"
    assert st.inserts[0][1]["platform_post_id"] == "p9"

    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: pytest.fail("cap exceeded"))
    capped, stats = Store(posted_today=2), {}
    runner.publish_ready(capped, 7, d, CFG, stats)
    assert stats["over_daily_limit"] == 1 and capped.draft["status"] == "pending"


def test_auto_publish_never_posts_without_database(monkeypatch):
    from newsbot import runner
    from newsbot.models import Draft

    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: pytest.fail("no DB means no cap check"))
    stats = {}
    runner.publish_ready(None, None, Draft(headline="H", body="b"), CFG, stats)
    assert stats["publish_blocked"] == 1


def test_second_job_cannot_claim_a_draft_already_being_posted(monkeypatch):
    from newsbot import runner
    from newsbot.models import Draft

    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: pytest.fail("posted twice"))
    st = Store(status="approved")  # another job already claimed it
    st.draft["status"] = "approved"
    stats = {}
    runner.publish_ready(st, 7, Draft(headline="H", body="b"), CFG, stats)
    assert stats["already_claimed"] == 1
