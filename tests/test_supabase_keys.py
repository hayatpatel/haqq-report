from unittest.mock import MagicMock

import requests

from newsbot import __main__ as cli
from newsbot import owner_feed, store


def test_new_style_key_uses_apikey_header_only():
    h = store.auth_headers("sb_secret_abc")
    assert h["apikey"] == "sb_secret_abc" and "Authorization" not in h


def test_legacy_jwt_key_keeps_bearer():
    h = store.auth_headers("eyJhbGciOi.payload.sig")
    assert h["Authorization"] == "Bearer eyJhbGciOi.payload.sig"


def _http_error(status, body):
    r = MagicMock(); r.status_code = status; r.json.return_value = body
    return requests.HTTPError("x", response=r)


def test_error_description_has_status_and_message_but_no_key():
    d = store.describe_http_error(_http_error(401, {"message": "Invalid API key", "code": "PGRST301"}))
    assert d.startswith("HTTP 401") and "Invalid API key" in d


def test_db_check_reports_each_table(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co"); monkeypatch.setenv("SUPABASE_KEY", "sb_secret_abc")
    sent = []
    monkeypatch.setattr(owner_feed, "alert_owner", sent.append)

    def fake_get(url, **kw):
        r = MagicMock()
        if url.endswith("/tokens"):
            r.raise_for_status.side_effect = _http_error(401, {"message": "Invalid API key", "code": "x"})
        return r
    monkeypatch.setattr(store.requests, "get", fake_get)
    assert cli.main(["db-check"]) == 1
    out = capsys.readouterr().out
    assert "tokens: FAILED HTTP 401" in out and "sources: ok" in out and "1 of 11" in out and sent


def test_db_check_all_ok(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co"); monkeypatch.setenv("SUPABASE_KEY", "sb_secret_abc")
    monkeypatch.setattr(owner_feed, "alert_owner", lambda t: None)
    monkeypatch.setattr(store.requests, "get", lambda url, **kw: MagicMock())
    assert cli.main(["db-check"]) == 0 and "ALL OK" in capsys.readouterr().out
