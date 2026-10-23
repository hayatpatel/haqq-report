from datetime import datetime, timezone
from unittest.mock import MagicMock

from newsbot import __main__ as cli
from newsbot import ai, owner_feed, pipeline, runner, runlog
from newsbot.models import Article, Draft, Lane, Tier

CFG = {"mode": "review", "run": {"max_articles": 8, "max_telegram_drafts": 2, "dedupe_days": 7},
       "owner_feed": {"log_file": "always"}, "limits": {"INGEST_MAX_AGE_HOURS": 48}, "enable": {}}


def art(n):
    return Article(url=f"https://e.com/{n}", title=f"Story {n}", summary="UN OCHA said x.", source_name="UN OCHA",
                   tier=Tier.A, published=datetime.now(timezone.utc))


class FakeStore:
    def __init__(self, existing=()):
        self.existing, self.rows = list(existing), {"articles": [], "claims": [], "drafts": [], "run_logs": []}

    def select_raw(self, table, params):
        return [{"url": u, "processed": True} for u in self.existing] + \
               [{"url": u, "processed": False} for u in getattr(self, "unprocessed", [])]

    def update(self, table, match, values):
        self.rows.setdefault("updates", []).append((table, match, values))

    def upsert(self, table, row, on_conflict):
        self.rows[table].append(row); return {"id": len(self.rows[table])}

    def insert(self, table, row):
        self.rows[table].append(row); return row


def outcome(url, action="needs_approval"):
    d = Draft(headline="H", body="UN OCHA said x.", lane=Lane.AMBER, source_urls=[url])
    return pipeline.Outcome(url, action, "review mode", d)


def test_run_stores_everything_and_previews_but_never_publishes(monkeypatch):
    previews = []
    monkeypatch.setattr(owner_feed, "draft_preview", lambda *a, **k: previews.append(a))
    monkeypatch.setattr(pipeline, "process_article", lambda a, ai_call, cfg: outcome(a.url))
    monkeypatch.setattr(pipeline, "publish_draft", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no publish")))
    store = FakeStore(existing=["https://e.com/1"])  # story 1 was stored by an earlier run
    stats = runner.run(store=store, ai_call=lambda p: "", cfg=CFG, fetcher=lambda priority: [art(1), art(2), art(3)])
    assert stats["fetched"] == 3 and stats["new"] == 2 and stats["stored"] == 2
    assert len(store.rows["drafts"]) == 2 and store.rows["drafts"][0]["status"] == "pending"
    assert stats["previews_sent"] == 2 and len(previews) == 2


def test_preview_cap_respected(monkeypatch):
    sent = []
    monkeypatch.setattr(owner_feed, "draft_preview", lambda *a, **k: sent.append(1))
    monkeypatch.setattr(pipeline, "process_article", lambda a, ai_call, cfg: outcome(a.url))
    runner.run(store=FakeStore(), ai_call=lambda p: "", cfg=CFG, fetcher=lambda p: [art(i) for i in range(5)])
    assert len(sent) == 2  # max_telegram_drafts


def test_ai_outage_stops_drafting_but_keeps_stored_articles(monkeypatch):
    def boom(a, ai_call, cfg):
        raise ai.AIUnavailable("all providers failed")
    monkeypatch.setattr(pipeline, "process_article", boom)
    store = FakeStore()
    stats = runner.run(store=store, ai_call=lambda p: "", cfg=CFG, fetcher=lambda p: [art(1), art(2)])
    assert stats["stored"] == 2 and "all providers failed" in stats["ai_error"] and not store.rows["drafts"]


def test_run_works_without_database(monkeypatch):
    monkeypatch.setattr(owner_feed, "draft_preview", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "process_article", lambda a, ai_call, cfg: outcome(a.url))
    stats = runner.run(store=None, ai_call=lambda p: "", cfg=CFG, fetcher=lambda p: [art(1)])
    assert stats["new"] == 1 and stats["stored"] == 0 and stats["previews_sent"] == 1


def test_runlog_writes_redacted_file_saves_to_db_and_sends(monkeypatch, tmp_path):
    secret = "SuperSecretTokenValue123456"
    monkeypatch.setenv("FB_PAGE_TOKEN", secret)
    sent, store = [], FakeStore()
    monkeypatch.setattr(owner_feed, "send_log", lambda path, caption="": sent.append((path, caption)))
    with runlog.RunLog("run", store, log_dir=tmp_path, cfg=CFG) as rl:
        runlog.log.info("calling graph with token %s", secret)
        rl.summary["new"] = 3
    text = rl.path.read_text()
    assert secret not in text and "***" in text and "started" in text and "finished" in text
    assert store.rows["run_logs"][0]["status"] == "ok" and store.rows["run_logs"][0]["summary"] == {"new": 3}
    assert sent and sent[0][0] == rl.path


def test_runlog_on_error_policy_and_exception_recorded(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(owner_feed, "send_log", lambda path, caption="": sent.append(1))
    cfg = {"owner_feed": {"log_file": "on_error"}}
    with runlog.RunLog("ok-run", None, log_dir=tmp_path, cfg=cfg):
        pass
    assert sent == []
    try:
        with runlog.RunLog("bad-run", None, log_dir=tmp_path, cfg=cfg) as rl:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert sent == [1] and "boom" in rl.path.read_text() and rl.status == "error"


def test_runlog_survives_missing_run_logs_table(monkeypatch, tmp_path):
    monkeypatch.setattr(owner_feed, "send_log", lambda *a, **k: None)
    store = MagicMock(); store.insert.side_effect = RuntimeError("404 relation run_logs")
    with runlog.RunLog("run", store, log_dir=tmp_path, cfg=CFG):
        pass  # must not raise


def test_send_log_noop_without_telegram(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    f = tmp_path / "x.log"; f.write_text("hi")
    assert owner_feed.send_log(f) is False


def test_cli_run_command(monkeypatch):
    monkeypatch.setattr(cli, "_store_or_none", lambda: None)
    monkeypatch.setattr("newsbot.runner.run", lambda **k: {"new": 1, "ai_error": ""})
    monkeypatch.setattr(owner_feed, "send_log", lambda *a, **k: None)
    assert cli.main(["run"]) == 1  # no Supabase configured is reported as an error
    monkeypatch.setattr(cli, "_store_or_none", lambda: FakeStore())
    assert cli.main(["run", "--priority"]) == 0


def test_processed_articles_are_marked_and_unprocessed_are_retried(monkeypatch):
    monkeypatch.setattr(owner_feed, "draft_preview", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "process_article", lambda a, ai_call, cfg: outcome(a.url))
    store = FakeStore(existing=["https://e.com/1"])
    store.unprocessed = ["https://e.com/2"]  # stored during an earlier AI outage
    stats = runner.run(store=store, ai_call=lambda p: "", cfg=CFG, fetcher=lambda p: [art(1), art(2)])
    assert stats["new"] == 1  # story 2 is retried, story 1 is skipped
    assert any(u[2] == {"processed": True} for u in store.rows["updates"])


def test_ai_outage_leaves_articles_unprocessed(monkeypatch):
    def boom(a, ai_call, cfg):
        raise ai.AIUnavailable("down")
    monkeypatch.setattr(pipeline, "process_article", boom)
    store = FakeStore()
    runner.run(store=store, ai_call=lambda p: "", cfg=CFG, fetcher=lambda p: [art(1)])
    assert not store.rows.get("updates")
