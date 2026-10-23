"""Run log: a detailed, secret-free log file per run, saved to Supabase and sent to the owner on Telegram.

Never raises: logging problems must not break a run.
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import owner_feed
from .config import ROOT, load_config, redact

LOG_DIR = ROOT / "logs"
MAX_DB_CHARS = 200_000
log = logging.getLogger("haqq")


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


class RunLog:
    """Context manager: with RunLog("run", store) as rl: ... rl.summary["articles"] = 3"""

    def __init__(self, name: str, store=None, log_dir: Path | None = None, cfg: dict | None = None):
        self.name, self.store, self.cfg = name, store, cfg
        self.dir = Path(log_dir) if log_dir else LOG_DIR
        self.started = datetime.now(timezone.utc)
        self.path = self.dir / f"{name}-{self.started.strftime('%Y%m%dT%H%M%SZ')}.log"
        self.summary: dict = {}
        self.status = "ok"
        self.idle = False  # nothing happened (e.g. no taps): do not store or send this log
        self._handlers: list[logging.Handler] = []
        self._old_level = log.level

    def __enter__(self) -> "RunLog":
        self.dir.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        for h in (logging.FileHandler(self.path, encoding="utf-8"), logging.StreamHandler()):
            h.setFormatter(fmt); h.addFilter(_Redact())
            h.setLevel(logging.DEBUG if isinstance(h, logging.FileHandler) else logging.INFO)
            log.addHandler(h); self._handlers.append(h)
        log.setLevel(logging.DEBUG)
        log.info("run '%s' started", self.name)
        return self

    def fail(self, reason: str) -> None:
        self.status = "error"
        log.error("%s", reason)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.status = "error"
            log.error("unhandled %s: %s\n%s", exc_type.__name__, exc, "".join(traceback.format_tb(tb))[-3000:])
        log.info("run '%s' finished: status=%s summary=%s", self.name, self.status, self.summary)
        for h in self._handlers:
            h.flush(); log.removeHandler(h); h.close()
        log.setLevel(self._old_level)
        self._finish()
        return False  # never swallow the caller's exception

    def _finish(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        if self.idle and self.status == "ok":
            return
        if self.store is not None:
            try:
                self.store.insert("run_logs", {
                    "name": self.name, "started_at": self.started.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(), "status": self.status,
                    "summary": self.summary, "log_text": text[-MAX_DB_CHARS:]})
            except Exception as exc:  # noqa: BLE001 - e.g. run_logs table not created yet
                print(f"run log not saved to Supabase: {type(exc).__name__}")
        cfg = self.cfg or load_config()
        policy = cfg.get("owner_feed", {}).get("log_file", "always")
        if policy == "always" or (policy == "on_error" and self.status != "ok"):
            owner_feed.send_log(self.path, owner_feed.format_run_summary(self.name, self.status, self.summary))
