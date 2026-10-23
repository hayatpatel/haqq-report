import os
import time

import pytest

from newsbot import approvals, fbtoken
from newsbot.publish import meta_fb

NEVER = {"is_valid": True, "type": "PAGE", "expires_at": 0, "profile_id": "111",
         "scopes": ["pages_manage_posts", "pages_manage_engagement"]}


class Store:
    def __init__(self):
        self.rows, self.deleted = {}, []

    def upsert(self, table, row, on_conflict):
        self.rows[row["key"]] = row["value"]; return row

    def select_raw(self, table, params):
        return [{"key": k, "value": v} for k, v in self.rows.items()]

    def delete(self, table, params):
        self.deleted.append(params); return 1


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {"FB_APP_ID": "1", "FB_APP_SECRET": "s", "FB_PAGE_ID": "111"}.items():
        monkeypatch.setenv(k, v)


def test_user_token_becomes_never_expiring_page_token(monkeypatch):
    seen = {}
    monkeypatch.setattr(meta_fb, "debug_token", lambda t: {**NEVER, "type": "USER"} if t == "USERTOK" else NEVER)
    monkeypatch.setattr(meta_fb, "exchange_long_lived", lambda t: "LONGUSER")
    monkeypatch.setattr(meta_fb, "page_from_user_token", lambda t, pid: seen.update(t=t) or {"id": pid, "name": "Haqq Report", "access_token": "PAGETOK"})
    st = Store()
    reply = fbtoken.update_from_text("USERTOK", st)
    assert reply.startswith("✅ FACEBOOK TOKEN · saved and never expires") and "Expires: never" in reply
    assert st.rows["FB_PAGE_TOKEN"] == "PAGETOK" and seen["t"] == "LONGUSER"
    assert "USERTOK" not in reply and "PAGETOK" not in reply


def test_expiring_page_token_is_rejected(monkeypatch):
    soon = {**NEVER, "expires_at": int(time.time()) + 3600}
    monkeypatch.setattr(meta_fb, "debug_token", lambda t: soon)
    st = Store()
    reply = fbtoken.update_from_text("SHORT", st)
    assert reply.startswith("❌") and "not saved" in reply and "Paste a User token" in reply and st.rows == {}


def test_missing_posting_permission_and_wrong_page_are_rejected(monkeypatch):
    monkeypatch.setattr(meta_fb, "debug_token", lambda t: {**NEVER, "scopes": ["pages_show_list"]})
    assert "Missing permission: pages_manage_posts" in fbtoken.update_from_text("T", Store())
    monkeypatch.setattr(meta_fb, "debug_token", lambda t: {**NEVER, "profile_id": "999"})
    assert "different Page" in fbtoken.update_from_text("T", Store())


def test_comment_permission_missing_is_a_note_not_a_failure(monkeypatch):
    monkeypatch.setattr(meta_fb, "debug_token", lambda t: {**NEVER, "scopes": ["pages_manage_posts"]})
    st = Store()
    assert "pages_manage_engagement is missing" in fbtoken.update_from_text("T", st) and st.rows


def test_override_replaces_env_and_reset_removes_it(monkeypatch):
    monkeypatch.setenv("FB_PAGE_TOKEN", "OLD")
    st = Store(); st.rows["FB_PAGE_TOKEN"] = "NEW"
    fbtoken.apply_overrides(st)
    assert os.environ["FB_PAGE_TOKEN"] == "NEW"
    assert "reset" in fbtoken.update_from_text("reset", st) and st.deleted


def test_only_the_owner_can_send_commands_and_token_message_is_deleted(monkeypatch):
    calls, sent = [], []
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
    monkeypatch.setattr(approvals, "_call", lambda m, d: calls.append(m) or {})
    monkeypatch.setattr(approvals.owner_feed, "message", sent.append)
    monkeypatch.setattr(approvals.fbtoken, "update_from_text", lambda raw, st: "✅ FACEBOOK TOKEN · saved")
    msg = {"from": {"id": 42}, "chat": {"id": 42}, "message_id": 5, "text": "/fbtoken SECRET"}
    assert approvals.handle_message(Store(), msg).startswith("fbtoken")
    assert calls == ["deleteMessage"] and sent == ["✅ FACEBOOK TOKEN · saved"]
    assert approvals.handle_message(Store(), {**msg, "from": {"id": 7}}) == "ignored: not the owner"
