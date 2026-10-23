from datetime import datetime, timedelta, timezone

from newsbot import cleanup, duplicates, ingest
from newsbot.models import Article, Tier

NOW = datetime.now(timezone.utc)


def test_same_story_different_wording_is_a_duplicate():
    a = "Five Israelis wounded, two suspected Palestinian attackers killed in West Bank shooting"
    assert duplicates.find_duplicate("Two Palestinian attackers killed, five Israelis wounded in West Bank shooting", [a])
    assert not duplicates.find_duplicate("Smotrich orders demolitions near Hebron shooting site", [a])
    assert not duplicates.find_duplicate("Insulin shortage hits Pakistan", [a])


def test_undated_and_old_items_are_not_posted():
    def art(n, pub):
        return Article(url=f"https://e.com/{n}", title="t", summary="", source_name="s", tier=Tier.A, published=pub)
    cfg = {"limits": {"INGEST_MAX_AGE_HOURS": 48}}
    kept = ingest.within_window([art(1, NOW - timedelta(hours=47)), art(2, NOW - timedelta(hours=49)), art(3, None)], cfg=cfg)
    assert [a.url[-1] for a in kept] == ["1"]


class DB:
    def __init__(self):
        self.deleted = []

    def select_raw(self, table, params):
        if table == "articles":
            return [{"id": 1}, {"id": 2}]
        if table == "drafts" and "article_id" in params:
            return [{"id": 9}]
        if table == "drafts":
            return [{"id": 7}]
        return []

    def delete(self, table, params):
        self.deleted.append((table, params)); return 1


def test_cleanup_deletes_in_dependency_order_and_only_news_tables():
    db = DB()
    out = cleanup.run(db, {"retention": {"hours": 72}})
    order = [t for t, _ in db.deleted]
    assert order == ["posts", "drafts", "articles", "run_logs"]
    assert db.deleted[0][1] == {"draft_id": "in.(7,9)"} and out["articles"] == 1
    assert cleanup.run(None, {}) == {"articles": 0, "drafts": 0, "posts": 0, "run_logs": 0}
