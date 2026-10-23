from unittest.mock import MagicMock

import pytest

from newsbot import ai
from newsbot import __main__ as cli
from newsbot import owner_feed


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ai._DEAD.clear()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_testkey_123456"); monkeypatch.setenv("GEMINI_API_KEY", "AQ.testkey_123456")
    yield
    ai._DEAD.clear()


def resp(status, body):
    r = MagicMock(); r.ok = status < 400; r.status_code = status; r.json.return_value = body; r.text = str(body)
    return r


def test_dead_model_is_skipped_and_next_one_used(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        model = kw["json"]["model"]; calls.append(model)
        if model == "openai/gpt-oss-120b":
            return resp(404, {"error": {"message": "The model does not exist or you do not have access"}})
        return resp(200, {"choices": [{"message": {"content": "ready"}}]})
    monkeypatch.setattr(ai.requests, "post", fake_post)
    assert ai.generate("hi") == "ready"
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    calls.clear()
    ai.generate("again")
    assert calls == ["openai/gpt-oss-20b"]  # the dead model is not retried


def test_error_text_includes_provider_message_but_not_key(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", lambda url, **kw: resp(404, {"error": {"message": "model gone gsk_testkey_123456"}}))
    with pytest.raises(ai.AIUnavailable) as e:
        ai.generate("hi")
    text = str(e.value)
    assert "HTTP 404" in text and "model gone" in text and "gsk_testkey_123456" not in text


def test_falls_back_to_gemini_when_groq_models_all_fail(monkeypatch):
    def fake_post(url, **kw):
        if "groq" in url:
            return resp(403, {"error": {"message": "not allowed"}})
        return resp(200, {"candidates": [{"content": {"parts": [{"text": "from gemini"}]}}]})
    monkeypatch.setattr(ai.requests, "post", fake_post)
    assert ai.generate("hi") == "from gemini"


def test_old_single_model_setting_still_works():
    assert ai._models({"groq_model": "x"}, "groq") == ["x"]
    assert ai._models({"groq_models": ["a", "b"], "groq_model": "x"}, "groq") == ["a", "b"]


def test_ai_check_reports_usable_models(monkeypatch, capsys):
    monkeypatch.setattr(owner_feed, "alert_owner", lambda t: None)
    monkeypatch.setattr(ai, "list_models", lambda: {"groq": ["openai/gpt-oss-20b", "x"], "gemini": ["gemini-3.5-flash-lite"]})
    monkeypatch.setattr(ai, "generate", lambda p: "ready")
    assert cli.main(["ai-check"]) == 0
    out = capsys.readouterr().out
    assert "configured and usable: openai/gpt-oss-20b" in out and "gemini-3.5-flash-lite" in out and "test call: OK" in out


def test_ai_check_failure_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(owner_feed, "alert_owner", lambda t: None)
    monkeypatch.setattr(ai, "list_models", lambda: {})
    monkeypatch.setattr(ai, "generate", lambda p: (_ for _ in ()).throw(ai.AIUnavailable("all failed")))
    assert cli.main(["ai-check"]) == 1
