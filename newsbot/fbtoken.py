"""Update the Facebook Page token from Telegram, and prove it never expires.

You send the bot:  /fbtoken <token>   (a User token from the Graph API Explorer, or a Page token)
The bot: checks the token, turns a User token into the never-expiring Page token for FB_PAGE_ID, checks
expiry and permissions, saves it in the Supabase `settings` table (it overrides the GitHub secret from the
next run), deletes your message, and replies with a plain report. `/fbtoken reset` goes back to the secret.
`/tokenstatus` shows the token in use.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .config import optional_env, redact
from .publish import meta_fb
from .publish.base import PublishError

SETTING = "FB_PAGE_TOKEN"
MUST = ["pages_manage_posts"]                 # posting is impossible without it
SHOULD = ["pages_manage_engagement"]          # source comment; the caption fallback works without it

HELP = ("🔑 FACEBOOK TOKEN · how to update\n"
        "1. Open developers.facebook.com/tools/explorer and choose your Haqq Report app.\n"
        "2. User or Page: User Token. Add permissions: pages_show_list, pages_manage_posts, "
        "pages_manage_engagement, pages_read_engagement.\n"
        "3. Click Generate Access Token, approve, and copy it.\n"
        "4. Send me:  /fbtoken <paste token>\n"
        "I turn it into the never-expiring Page token, check it, save it, and delete your message.\n"
        "Other commands: /tokenstatus, /fbtoken reset")


def apply_overrides(store) -> None:
    """Use the token saved from Telegram instead of the GitHub secret. Never raises."""
    if store is None:
        return
    try:
        rows = store.select_raw("settings", {"key": f"eq.{SETTING}"})
        if rows and rows[0].get("value"):
            os.environ[SETTING] = rows[0]["value"]
    except Exception:  # noqa: BLE001
        pass


def expiry_text(data: dict) -> tuple[str, bool]:
    """(plain words, is_never_expiring)."""
    exp = int(data.get("expires_at") or 0)
    if exp == 0:
        return "never ✅", True
    when = datetime.fromtimestamp(exp, timezone.utc)
    hours = (when - datetime.now(timezone.utc)).total_seconds() / 3600
    left = f"{hours:.0f} hours" if hours < 72 else f"{hours / 24:.0f} days"
    return f"in {left} ({when:%d %b %Y}) ❌ not permanent", False


def _scopes(data: dict) -> list[str]:
    return list(data.get("scopes") or [])


def _report(title: str, data: dict, name: str = "") -> str:
    exp, _ = expiry_text(data)
    scopes = _scopes(data)
    perms = " · ".join(f"{p} {'✅' if p in scopes else '❌'}" for p in MUST + SHOULD)
    lines = [title, f"Page: {name or data.get('profile_id', '?')}", f"Expires: {exp}", f"Permissions: {perms}"]
    da = int(data.get("data_access_expires_at") or 0)
    if da:
        lines.append(f"Data access renews by: {datetime.fromtimestamp(da, timezone.utc):%d %b %Y} (posting is not affected)")
    return "\n".join(lines)


def _fail(why: str, fix: str = "") -> str:
    return "❌ FACEBOOK TOKEN · not saved\nWhy: " + why + (f"\nFix: {fix}" if fix else "")


def update_from_text(raw: str, store) -> str:
    """Handle the text after /fbtoken. Returns the reply for Telegram."""
    raw = raw.strip()
    if not raw:
        return HELP
    if raw.lower() == "reset":
        store.delete("settings", {"key": f"eq.{SETTING}"})
        return "✅ FACEBOOK TOKEN · reset\nThe GitHub secret FB_PAGE_TOKEN is used again from the next run."
    page_id = optional_env("FB_PAGE_ID")
    if not (optional_env("FB_APP_ID") and optional_env("FB_APP_SECRET") and page_id):
        return _fail("FB_APP_ID, FB_APP_SECRET or FB_PAGE_ID is missing in this workflow.",
                     "Add them as GitHub secrets (they are already used by the health check).")
    secrets = [raw]
    try:
        info = meta_fb.debug_token(raw)
        if not info.get("is_valid"):
            return _fail("Facebook says this token is not valid (expired, revoked or mistyped).",
                         "Generate a new one in the Graph API Explorer and send it again.")
        name = ""
        if info.get("type") == "USER":
            long_user = meta_fb.exchange_long_lived(raw)
            secrets.append(long_user)
            page = meta_fb.page_from_user_token(long_user, page_id)
            if not page:
                return _fail(f"This Facebook user does not manage the Page {page_id}.",
                             "Log in as the Page admin in the Graph API Explorer and tick the Page when approving.")
            token, name = page["access_token"], page.get("name", "")
        else:
            token = raw
        secrets.append(token)
        final = meta_fb.debug_token(token)
        if not final.get("is_valid"):
            return _fail("The Page token did not validate.")
        if str(final.get("profile_id", page_id)) != str(page_id):
            return _fail(f"This token belongs to a different Page ({final.get('profile_id')}).")
        missing = [p for p in MUST if p not in _scopes(final)]
        if missing:
            return _fail("Missing permission: " + ", ".join(missing) + ".",
                         "Generate the token again and tick these permissions.")
        exp_text, never = expiry_text(final)
        if not never:
            return _fail(f"This token expires {exp_text.split(' ❌')[0]}.",
                         "Paste a User token instead. I will convert it into the never-expiring Page token.")
    except (PublishError, KeyError) as exc:
        text = str(exc)
        for sec in secrets:
            text = text.replace(sec, "***")
        return _fail(redact(text)[:250])
    store.upsert("settings", {"key": SETTING, "value": token}, "key")
    note = "" if all(p in _scopes(final) for p in SHOULD) else \
        "\nNote: pages_manage_engagement is missing, so the source link goes in the caption, not a comment."
    return _report("✅ FACEBOOK TOKEN · saved and never expires", final, name) + note + "\nIt is used from the next run."


def status(store=None) -> str:
    """Details of the token currently in use (after any override)."""
    apply_overrides(store)
    token = optional_env("FB_PAGE_TOKEN")
    if not token:
        return _fail("No FB_PAGE_TOKEN is set.", "Send /fbtoken to see how.")
    try:
        data = meta_fb.debug_token(token)
    except Exception as exc:  # noqa: BLE001
        return _fail(redact(str(exc).replace(token, "***"))[:250])
    if not data.get("is_valid"):
        return _fail("The token in use is not valid. Posting to Facebook will fail.", "Send /fbtoken to replace it.")
    _, never = expiry_text(data)
    title = "✅ FACEBOOK TOKEN · in use" if never else "⚠️ FACEBOOK TOKEN · in use, will expire"
    return _report(title, data)
