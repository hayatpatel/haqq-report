from datetime import datetime, timezone

import requests

from newsbot import health

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


class BrokenStore:
    def token_issued_at(self, platform):
        raise requests.HTTPError("404 Not Found: relation tokens")


class EmptyStore:
    def __init__(self):
        self.recorded = {}

    def token_issued_at(self, platform):
        return None

    def record_token_refresh(self, platform, when=None):
        self.recorded[platform] = when


def test_missing_tables_reported_not_crashed(monkeypatch):
    monkeypatch.setenv("IG_TOKEN", "x" * 20)
    line = health.check_age_based("instagram", "instagram", lambda: "n", "IG_TOKEN", BrokenStore(), NOW)
    assert line.startswith("FAIL") and "db-check" in line


def test_first_run_records_today_and_is_ok(monkeypatch):
    monkeypatch.setenv("IG_TOKEN", "x" * 20)
    store = EmptyStore()
    line = health.check_age_based("instagram", "instagram", lambda: "n", "IG_TOKEN", store, NOW)
    assert line.startswith("OK") and store.recorded["instagram"] == NOW


def test_no_store_is_a_failure_line(monkeypatch):
    monkeypatch.setenv("TH_TOKEN", "x" * 20)
    assert health.check_age_based("threads", "threads", lambda: "n", "TH_TOKEN", None, NOW).startswith("FAIL")
