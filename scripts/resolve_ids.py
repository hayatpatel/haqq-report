#!/usr/bin/env python3
"""Look up IG_USER_ID and TH_USER_ID from the tokens already in the environment (.env).
Prints only the IDs (not secret). Usage: set -a; . ./.env; set +a; python scripts/resolve_ids.py"""
import os
import sys

import requests


def get(url, token, fields):
    r = requests.get(url, params={"fields": fields, "access_token": token}, timeout=20)
    return r.json() if r.ok else {"error": r.json().get("error", {}).get("message", r.status_code)}


if os.environ.get("IG_TOKEN"):
    d = get("https://graph.instagram.com/v21.0/me", os.environ["IG_TOKEN"], "user_id,username")
    print("IG_USER_ID =", d.get("user_id") or d.get("id") or d, "| username:", d.get("username"))
if os.environ.get("TH_TOKEN"):
    d = get("https://graph.threads.net/v1.0/me", os.environ["TH_TOKEN"], "id,username")
    print("TH_USER_ID =", d.get("id") or d, "| username:", d.get("username"))
sys.exit(0)
