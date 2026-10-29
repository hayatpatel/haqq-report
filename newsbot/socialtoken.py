"""Check and replace the Instagram and Threads tokens from Telegram (like /fbtoken does for Facebook).

/igtoken <token>   /thtoken <token>   save a new token (checked first; a short-lived one is exchanged for a 60-day one)
/igtoken reset     /thtoken reset     go back to the GitHub secret
/tokenstatus       asks Facebook, Instagram and Threads whether each token works right now and shows the exact error
"""
from __future__ import annotations

import os

import requests

from .config import optional_env, redact

KINDS = {
    "instagram": {"cmd": "/igtoken", "setting": "IG_TOKEN", "id": "IG_USER_ID", "app_secret": "IG_APP_SECRET",
                  "me": "https://graph.instagram.com/v21.0/me", "fields": "user_id,username",
                  "exchange": ("https://graph.instagram.com/access_token", "ig_exchange_token"),
                  "howto": "Meta developer dashboard, your app, Instagram, API setup with Instagram login, "
                           "Generate token for your Instagram account (needs instagram_business_basic and instagram_business_content_publish)."},
    "threads": {"cmd": "/thtoken", "setting": "TH_TOKEN", "id": "TH_USER_ID", "app_secret": "TH_APP_SECRET",
                "me": "https://graph.threads.net/v1.0/me", "fields": "id,username",
                "exchange": ("https://graph.threads.net/access_token", "th_exchange_token"),
                "howto": "Meta developer dashboard, your Threads app, Use cases, Threads API, generate the token for your account "
                         "(needs threads_basic and threads_content_publish)."},
}


def apply_overrides(store) -> None:
    if store is None:
        return
    for key in [k["setting"] for k in KINDS.values()] + ["IG_ROUTE", "IG_BUSINESS_ID"]:
        try:
            rows = store.select_raw("settings", {"key": f"eq.{key}"})
            if rows and rows[0].get("value"):
                os.environ[key] = rows[0]["value"]
        except Exception:  # noqa: BLE001
            pass
    return


def _me(kind: str, token: str) -> dict:
    k = KINDS[kind]
    resp = requests.get(k["me"], params={"fields": k["fields"], "access_token": token}, timeout=30)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", data)
        raise RuntimeError(f"{err.get('message', err)} (code {err.get('code', resp.status_code)})")
    return data


def check(kind: str) -> str:
    k = KINDS[kind]
    token = optional_env(k["setting"])
    name = kind.title()
    if not token:
        return f"❌ {name}: no token in this workflow ({k['setting']} missing)."
    try:
        data = _me(kind, token)
    except Exception as exc:  # noqa: BLE001
        return f"❌ {name}: token rejected. {redact(str(exc).replace(token, '***'))[:220]}\n   Fix: send {k['cmd']} <new token>."
    uid = str(data.get("user_id") or data.get("id") or "")
    expected = optional_env(k["id"])
    if expected and uid and uid != expected:
        return f"⚠️ {name}: token works but belongs to @{data.get('username')} ({uid}), not {k['id']} ({expected})."
    return f"✅ {name}: token works (@{data.get('username', '?')})."


def status() -> str:
    route = ("Instagram posts through the Facebook Page token (never expires)." if optional_env("IG_ROUTE") == "page"
             else "Instagram posts with the Instagram login token (60 days, auto-refreshed every 20 days).")
    return route + "\n" + "\n".join(check(k) for k in KINDS)


def update_from_text(kind: str, raw: str, store) -> str:
    k = KINDS[kind]
    raw = raw.strip()
    name = kind.upper()
    if not raw:
        return f"🔑 {name} TOKEN · how to update\n{k['howto']}\nThen send me:  {k['cmd']} <paste token>\nI check it, save it, and delete your message."
    if raw.lower() == "reset":
        store.delete("settings", {"key": f"eq.{k['setting']}"})
        return f"✅ {name} TOKEN · reset\nThe GitHub secret {k['setting']} is used again from the next run."
    token, secrets = raw, [raw]
    try:
        _me(kind, raw)
        secret = optional_env(k["app_secret"])
        if secret:  # a short-lived token becomes a 60-day one; an already long-lived one just stays as it is
            url, grant = k["exchange"]
            r = requests.get(url, params={"grant_type": grant, "client_secret": secret, "access_token": raw}, timeout=30)
            if r.status_code < 400 and r.json().get("access_token"):
                token = r.json()["access_token"]
                secrets.append(token)
        data = _me(kind, token)
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        for s in secrets:
            text = text.replace(s, "***")
        return f"❌ {name} TOKEN · not saved\nWhy: {redact(text)[:220]}\nFix: generate a new token and send it again."
    store.upsert("settings", {"key": k["setting"], "value": token}, "key")
    return f"✅ {name} TOKEN · saved\nAccount: @{data.get('username', '?')}. It is used from the next run."


# ---- never-expiring route: Instagram through the Facebook Page token ---------------------------------------
GRAPH = "https://graph.facebook.com/v21.0"
NEED = ["instagram_basic", "instagram_content_publish"]


def setup_page_route(arg: str, store) -> str:
    """/igsetup  : switch Instagram to the Facebook Page token (never expires). /igsetup login : back to the 60-day token."""
    from .publish import meta_fb

    if arg.strip().lower() in ("login", "reset", "off"):
        store.upsert("settings", {"key": "IG_ROUTE", "value": "login"}, "key")
        os.environ["IG_ROUTE"] = "login"
        return "✅ INSTAGRAM · back on the Instagram login token (60 days, auto-refreshed)."
    token, page_id = optional_env("FB_PAGE_TOKEN"), optional_env("FB_PAGE_ID")
    if not (token and page_id):
        return "❌ INSTAGRAM SETUP · FB_PAGE_TOKEN or FB_PAGE_ID is missing in this workflow."
    try:
        scopes = set(meta_fb.debug_token(token).get("scopes", []))
        missing = [p for p in NEED if p not in scopes]
        if missing:
            return ("❌ INSTAGRAM SETUP · the Facebook token lacks: " + ", ".join(missing) + ".\n"
                    "Fix: Graph API Explorer, User Token, add instagram_basic, instagram_content_publish, pages_show_list, "
                    "pages_read_engagement, pages_manage_posts, pages_manage_engagement, Generate, then send /fbtoken <token>, then /igsetup.")
        r = requests.get(f"{GRAPH}/{page_id}", params={"fields": "instagram_business_account{id,username}", "access_token": token}, timeout=30)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            return "❌ INSTAGRAM SETUP · " + redact(str(data.get("error", data)).replace(token, "***"))[:220]
        acct = data.get("instagram_business_account")
        if not acct:
            return ("❌ INSTAGRAM SETUP · this Facebook Page has no Instagram account linked.\n"
                    "Fix: Instagram app, Settings, Account type must be Professional (Business or Creator); then Facebook Page, "
                    "Settings, Linked accounts, Instagram, Connect. Then send /igsetup again.")
    except Exception as exc:  # noqa: BLE001
        return "❌ INSTAGRAM SETUP · " + redact(str(exc).replace(token, "***"))[:220]
    store.upsert("settings", {"key": "IG_BUSINESS_ID", "value": str(acct["id"])}, "key")
    store.upsert("settings", {"key": "IG_ROUTE", "value": "page"}, "key")
    os.environ["IG_BUSINESS_ID"], os.environ["IG_ROUTE"] = str(acct["id"]), "page"
    return (f"✅ INSTAGRAM · now posts through the Facebook Page token, which never expires.\n"
            f"Account: @{acct.get('username', '?')}. Used from the next run. Go back any time with /igsetup login.")


def refresh_due(store, days: int = 20) -> list[str]:
    """Refresh the Instagram and Threads tokens before their 60 days end, saving the new one in Supabase. Never raises."""
    from datetime import datetime, timezone

    out = []
    now = datetime.now(timezone.utc)
    for kind, k in KINDS.items():
        token = optional_env(k["setting"])
        if not token or store is None:
            continue
        try:
            stamp = store.get_setting(k["setting"] + "_refreshed", "")
            if stamp and (now - datetime.fromisoformat(stamp)).days < days:
                continue
            grant = "ig_refresh_token" if kind == "instagram" else "th_refresh_token"
            host = "graph.instagram.com" if kind == "instagram" else "graph.threads.net"
            r = requests.get(f"https://{host}/refresh_access_token", params={"grant_type": grant, "access_token": token}, timeout=30)
            new = r.json().get("access_token") if r.status_code < 400 else None
            if new:
                store.upsert("settings", {"key": k["setting"], "value": new}, "key")
                os.environ[k["setting"]] = new
                store.set_setting(k["setting"] + "_refreshed", now.isoformat())
                out.append(f"{kind} token refreshed")
            else:
                out.append(f"{kind} token refresh failed: {redact(r.text)[:120]}")
        except Exception as exc:  # noqa: BLE001
            out.append(f"{kind} token refresh error: {type(exc).__name__}")
    return out
