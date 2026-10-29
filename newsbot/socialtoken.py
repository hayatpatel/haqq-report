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
    for k in KINDS.values():
        try:
            rows = store.select_raw("settings", {"key": f"eq.{k['setting']}"})
            if rows and rows[0].get("value"):
                os.environ[k["setting"]] = rows[0]["value"]
        except Exception:  # noqa: BLE001
            pass


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
    return "\n".join(check(k) for k in KINDS)


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
