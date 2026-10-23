"""CLI: python -m newsbot <command>

Commands
  check-config   validate config.json and list which secrets are present (names only, never values)
  ingest         fetch feeds, dedupe, print a summary (add --priority for Palestine/priority feeds only)
  calendar       print Islamic occasions in the next 3 days and election-silence reminders
  corrections    regenerate the public corrections page
  health         token health check for Facebook, Instagram, Threads
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from . import calendar_islam, corrections, health, ingest, sources
from .config import load_config

SECRET_GROUPS = {
    "facebook": ["FB_APP_ID", "FB_APP_SECRET", "FB_PAGE_ID", "FB_PAGE_TOKEN"],
    "instagram": ["IG_APP_ID", "IG_APP_SECRET", "IG_USER_ID", "IG_TOKEN"],
    "threads": ["TH_APP_ID", "TH_APP_SECRET", "TH_USER_ID", "TH_TOKEN"],
    "token refresh": ["GH_PAT_SECRETS"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "TELEGRAM_CHANNEL_ID"],
    "bluesky": ["BSKY_HANDLE", "BSKY_APP_PASSWORD"],
    "supabase": ["SUPABASE_URL", "SUPABASE_KEY"],
    "ai": ["GROQ_API_KEY", "GEMINI_API_KEY"],
}


def cmd_check_config(_args) -> int:
    cfg = load_config()
    print(f"config.json OK | mode={cfg['mode']} | enabled="
          f"{[k for k, v in cfg['enable'].items() if v] or 'none'}")
    for group, names in SECRET_GROUPS.items():
        present = [n for n in names if os.environ.get(n)]
        missing = [n for n in names if n not in present]
        print(f"  {group:14} set {len(present)}/{len(names)}" + (f"  missing: {', '.join(missing)}" if missing else ""))
    return 0


def cmd_ingest(args) -> int:
    all_articles = []
    for src in sources.feeds(only_priority=args.priority):
        try:
            items = ingest.fetch_source(src)
        except Exception as exc:  # noqa: BLE001 - one dead feed must not stop the run
            print(f"  ! {src.name}: {type(exc).__name__}")
            continue
        print(f"  {src.name} (tier {src.tier.value}): {len(items)} items")
        all_articles.extend(items)
    fresh = ingest.dedupe(ingest.within_window(all_articles))
    print(f"total {len(all_articles)} fetched, {len(fresh)} after dedupe and 48h window")
    return 0


def cmd_calendar(_args) -> int:
    today = date.today()
    cfg = load_config()
    for line in calendar_islam.blackout_reminders(today, cfg):
        print(line)
    try:
        found = calendar_islam.upcoming_occasions(today)
    except Exception as exc:  # noqa: BLE001
        print(f"calendar lookup failed: {type(exc).__name__}")
        return 1
    print(json.dumps(found, indent=2) if found else "no occasions in the next 3 days")
    return 0


def cmd_corrections(_args) -> int:
    print(corrections.write_html())
    return 0


def cmd_health(_args) -> int:
    store = None
    try:
        from .store import Store

        store = Store()
    except Exception:  # noqa: BLE001
        print("Supabase not configured: token ages unavailable")
    lines, failed = health.run(store=store)
    print("\n".join(lines))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newsbot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config").set_defaults(fn=cmd_check_config)
    p = sub.add_parser("ingest")
    p.add_argument("--priority", action="store_true")
    p.set_defaults(fn=cmd_ingest)
    sub.add_parser("calendar").set_defaults(fn=cmd_calendar)
    sub.add_parser("corrections").set_defaults(fn=cmd_corrections)
    sub.add_parser("health").set_defaults(fn=cmd_health)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
