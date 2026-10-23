"""Instagram publisher (Instagram API with Instagram Login, no Facebook Page link).

Credentials (this module ONLY): IG_USER_ID, IG_TOKEN.
Images must be public JPEG URLs. Deleting a post via the API is NOT supported:
keep the manual removal step in the corrections process.
"""
from __future__ import annotations

import time
from typing import Callable

from ..config import load_config, require_env
from .base import PublishError, graph_request


def _base() -> str:
    return f"https://graph.instagram.com/{load_config()['meta']['graph_version']}"


def _creds() -> tuple[str, str]:
    env = require_env("IG_USER_ID", "IG_TOKEN")
    return env["IG_USER_ID"], env["IG_TOKEN"]


def wait_until_ready(container_id: str, token: str, *, tries: int = 12, delay: float = 5,
                     sleep: Callable[[float], None] = time.sleep) -> None:
    """Poll status_code until FINISHED (avoids 'container not ready')."""
    for _ in range(tries):
        out = graph_request("GET", f"{_base()}/{container_id}", params={"fields": "status_code", "access_token": token})
        status = out.get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"instagram container {container_id} status {status}")
        sleep(delay)
    raise PublishError(f"instagram container {container_id} not ready in time")


def _publish(creation_id: str, user_id: str, token: str) -> str:
    out = graph_request("POST", f"{_base()}/{user_id}/media_publish",
                        data={"creation_id": creation_id, "access_token": token})
    return out["id"]


def publish_image(image_url: str, caption: str) -> str:
    user_id, token = _creds()
    out = graph_request("POST", f"{_base()}/{user_id}/media",
                        data={"image_url": image_url, "caption": caption, "access_token": token})
    wait_until_ready(out["id"], token)
    return _publish(out["id"], user_id, token)


def publish_carousel(image_urls: list[str], caption: str) -> str:
    if not 2 <= len(image_urls) <= 10:
        raise PublishError("instagram carousel needs 2 to 10 images")
    user_id, token = _creds()
    children = []
    for url in image_urls:
        out = graph_request("POST", f"{_base()}/{user_id}/media",
                            data={"image_url": url, "is_carousel_item": "true", "access_token": token})
        children.append(out["id"])
    for child in children:
        wait_until_ready(child, token)
    parent = graph_request("POST", f"{_base()}/{user_id}/media", data={
        "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption, "access_token": token})
    wait_until_ready(parent["id"], token)
    return _publish(parent["id"], user_id, token)


def publish_story(image_url: str) -> str:
    user_id, token = _creds()
    out = graph_request("POST", f"{_base()}/{user_id}/media",
                        data={"media_type": "STORIES", "image_url": image_url, "access_token": token})
    wait_until_ready(out["id"], token)
    return _publish(out["id"], user_id, token)


def publishing_limit() -> dict:
    """Remaining daily API publishing quota."""
    user_id, token = _creds()
    return graph_request("GET", f"{_base()}/{user_id}/content_publishing_limit",
                         params={"fields": "quota_usage,config", "access_token": token})


def permalink(media_id: str) -> str:
    """Public link to a published post (for the owner's Telegram message)."""
    _, token = _creds()
    out = graph_request("GET", f"{_base()}/{media_id}", params={"fields": "permalink", "access_token": token})
    return out.get("permalink", "")
