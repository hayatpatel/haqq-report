"""Facebook Page publisher.

Credentials (this module ONLY): FB_PAGE_ID, FB_PAGE_TOKEN.
The Page token is derived from a long-lived user token and does not expire.
"""
from __future__ import annotations

from ..config import load_config, require_env
from .base import graph_request


def _base() -> str:
    return f"https://graph.facebook.com/{load_config()['meta']['graph_version']}"


def _creds() -> tuple[str, str]:
    env = require_env("FB_PAGE_ID", "FB_PAGE_TOKEN")
    return env["FB_PAGE_ID"], env["FB_PAGE_TOKEN"]


def post_photo(image_url: str, message: str) -> str:
    page_id, token = _creds()
    out = graph_request("POST", f"{_base()}/{page_id}/photos",
                        data={"url": image_url, "message": message, "access_token": token})
    return out.get("post_id") or out["id"]


def post_multi_photo(image_urls: list[str], message: str) -> str:
    """Upload each photo unpublished, then create one feed post with all of them."""
    page_id, token = _creds()
    media_ids = []
    for url in image_urls:
        out = graph_request("POST", f"{_base()}/{page_id}/photos",
                            data={"url": url, "published": "false", "access_token": token})
        media_ids.append(out["id"])
    data = {"message": message, "access_token": token}
    for i, mid in enumerate(media_ids):
        data[f"attached_media[{i}]"] = '{"media_fbid":"%s"}' % mid
    out = graph_request("POST", f"{_base()}/{page_id}/feed", data=data)
    return out["id"]


def post_story(image_url: str) -> str:
    """Facebook Page story: upload the image unpublished, then publish it as a photo story."""
    page_id, token = _creds()
    photo = graph_request("POST", f"{_base()}/{page_id}/photos",
                          data={"url": image_url, "published": "false", "access_token": token})
    out = graph_request("POST", f"{_base()}/{page_id}/photo_stories",
                        data={"photo_id": photo["id"], "access_token": token})
    return str(out.get("post_id") or out.get("id") or photo["id"])


def post_text(message: str) -> str:
    page_id, token = _creds()
    out = graph_request("POST", f"{_base()}/{page_id}/feed", data={"message": message, "access_token": token})
    return out["id"]


def first_comment(post_id: str, message: str) -> str:
    """Put the full source link here, not in the caption (plan fix #2)."""
    _, token = _creds()
    out = graph_request("POST", f"{_base()}/{post_id}/comments", data={"message": message, "access_token": token})
    return out["id"]


def update_message(post_id: str, message: str) -> None:
    _, token = _creds()
    graph_request("POST", f"{_base()}/{post_id}", data={"message": message, "access_token": token})


def delete_post(post_id: str) -> bool:
    """Retract a post. Facebook supports API deletion; Instagram and Threads do not."""
    _, token = _creds()
    out = graph_request("DELETE", f"{_base()}/{post_id}", params={"access_token": token})
    return bool(out.get("success", True))


def debug_token(token: str) -> dict:
    """Inspect ANY token (needs FB_APP_ID and FB_APP_SECRET): validity, type, expiry, scopes, profile."""
    env = require_env("FB_APP_ID", "FB_APP_SECRET")
    out = graph_request("GET", f"{_base()}/debug_token", params={
        "input_token": token, "access_token": f"{env['FB_APP_ID']}|{env['FB_APP_SECRET']}"})
    return out.get("data", out)


def exchange_long_lived(user_token: str) -> str:
    env = require_env("FB_APP_ID", "FB_APP_SECRET")
    out = graph_request("GET", f"{_base()}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": env["FB_APP_ID"], "client_secret": env["FB_APP_SECRET"],
        "fb_exchange_token": user_token})
    return out["access_token"]


def page_from_user_token(user_token: str, page_id: str) -> dict | None:
    """The Page entry (id, name, access_token) for page_id among the pages this user manages."""
    out = graph_request("GET", f"{_base()}/me/accounts", params={
        "fields": "id,name,access_token", "limit": 100, "access_token": user_token})
    return next((p for p in out.get("data", []) if p.get("id") == page_id), None)


def debug_page_token() -> dict:
    """Check the Page token (needs FB_APP_ID and FB_APP_SECRET)."""
    env = require_env("FB_APP_ID", "FB_APP_SECRET", "FB_PAGE_TOKEN")
    out = graph_request("GET", f"{_base()}/debug_token", params={
        "input_token": env["FB_PAGE_TOKEN"],
        "access_token": f"{env['FB_APP_ID']}|{env['FB_APP_SECRET']}",
    })
    return out.get("data", out)


def metrics(post_id: str) -> dict:
    """Public engagement counts for a Page post (reactions, comments, shares)."""
    _, token = _creds()
    out = graph_request("GET", f"{_base()}/{post_id}", params={
        "fields": "reactions.summary(true).limit(0),comments.summary(true).limit(0),shares", "access_token": token})
    return {"reactions": out.get("reactions", {}).get("summary", {}).get("total_count", 0),
            "comments": out.get("comments", {}).get("summary", {}).get("total_count", 0),
            "shares": out.get("shares", {}).get("count", 0)}
