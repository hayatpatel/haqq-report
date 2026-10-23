"""Threads publisher.

Credentials (this module ONLY): TH_USER_ID, TH_TOKEN.
A Threads account is tied to its Instagram account, so Instagram trouble can reach Threads.
"""
from __future__ import annotations

import time
from typing import Callable

from ..config import load_config, require_env
from .base import PublishError, graph_request


def _base() -> str:
    return f"https://graph.threads.net/{load_config()['meta']['threads_api_version']}"


def _creds() -> tuple[str, str]:
    env = require_env("TH_USER_ID", "TH_TOKEN")
    return env["TH_USER_ID"], env["TH_TOKEN"]


def _wait(container_id: str, token: str, *, tries: int = 12, delay: float = 5,
          sleep: Callable[[float], None] = time.sleep) -> None:
    for _ in range(tries):
        out = graph_request("GET", f"{_base()}/{container_id}", params={"fields": "status", "access_token": token})
        status = out.get("status")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"threads container {container_id} status {status}")
        sleep(delay)
    raise PublishError(f"threads container {container_id} not ready in time")


def publish(text: str, image_url: str | None = None, *, sleep: Callable[[float], None] = time.sleep) -> str:
    user_id, token = _creds()
    data = {"media_type": "IMAGE" if image_url else "TEXT", "text": text, "access_token": token}
    if image_url:
        data["image_url"] = image_url
    created = graph_request("POST", f"{_base()}/{user_id}/threads", data=data)
    sleep(load_config()["meta"]["threads_publish_wait_seconds"])
    _wait(created["id"], token, sleep=sleep)
    out = graph_request("POST", f"{_base()}/{user_id}/threads_publish",
                        data={"creation_id": created["id"], "access_token": token})
    return out["id"]
