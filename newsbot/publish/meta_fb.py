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


def debug_page_token() -> dict:
    """Check the Page token (needs FB_APP_ID and FB_APP_SECRET)."""
    env = require_env("FB_APP_ID", "FB_APP_SECRET", "FB_PAGE_TOKEN")
    out = graph_request("GET", f"{_base()}/debug_token", params={
        "input_token": env["FB_PAGE_TOKEN"],
        "access_token": f"{env['FB_APP_ID']}|{env['FB_APP_SECRET']}",
    })
    return out.get("data", out)
