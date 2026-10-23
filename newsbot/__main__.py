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
    "telegram (owner feed)": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"],
    "bluesky": ["BSKY_HANDLE", "BSKY_APP_PASSWORD"],
    "supabase": ["SUPABASE_URL", "SUPABASE_KEY"],
    "ai": ["GROQ_API_KEY", "GEMINI_API_KEY"],
}


OPTIONAL_GROUPS = {"bluesky"}  # not planned; module kept but disabled in config.json


def cmd_check_config(_args) -> int:
    cfg = load_config()
    print(f"config.json OK | mode={cfg['mode']} | enabled="
          f"{[k for k, v in cfg['enable'].items() if v] or 'none'}")
    for group, names in SECRET_GROUPS.items():
        present = [n for n in names if os.environ.get(n)]
        missing = [n for n in names if n not in present]
        note = "  (optional, not planned)" if group in OPTIONAL_GROUPS and missing else \
            (f"  missing: {', '.join(missing)}" if missing else "")
        print(f"  {group:14} set {len(present)}/{len(names)}" + note)
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


TEST_TEXT = "Haqq Report test post (please ignore). Source: Haqq Report internal connection test."


def cmd_test_post(args) -> int:
    """Manual connection test per platform. Dry run unless --live. Bypasses the enable flags on purpose.

    facebook: posts text, then deletes it again (unless --keep). instagram: needs --image-url (public JPEG),
    cannot be deleted by API so remove it by hand. threads: posts text, remove it by hand.
    """
    from . import owner_feed
    from .publish import instagram, meta_fb, threads

    platforms = ["facebook", "instagram", "threads"] if args.platform == "all" else [args.platform]
    mode = "LIVE" if args.live else "dry run"
    lines, failed = [f"Test post ({mode})"], False
    for name in platforms:
        try:
            if name == "instagram" and not args.image_url:
                raise ValueError("instagram needs --image-url (a public JPEG)")
            if not args.live:
                lines.append(f"  {name}: would post (dry run, nothing sent)")
                continue
            if name == "facebook":
                post_id = meta_fb.post_text(TEST_TEXT)
                if args.keep:
                    lines.append(f"  facebook: OK {post_id} (kept)")
                else:
                    deleted = meta_fb.delete_post(post_id)
                    lines.append(f"  facebook: OK {post_id}, deleted again: {deleted}")
            elif name == "instagram":
                lines.append(f"  instagram: OK {instagram.publish_image(args.image_url, TEST_TEXT)} (remove by hand)")
            else:
                lines.append(f"  threads: OK {threads.publish(TEST_TEXT)} (remove by hand)")
        except Exception as exc:  # noqa: BLE001 - report each platform on its own, never print secrets
            failed = True
            lines.append(f"  {name}: FAILED {type(exc).__name__}: {str(exc)[:200]}")
    print("\n".join(lines))
    owner_feed.message("\n".join(lines))
    return 1 if failed else 0


def _db_preflight() -> list[str]:
    """Secret-free checks on how the two Supabase secrets were stored. Never prints a value."""
    import re

    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", "")
    notes = []
    for name, val in (("SUPABASE_URL", url), ("SUPABASE_KEY", key)):
        if not val:
            notes.append(f"{name}: NOT SET (add it under Settings, Secrets and variables, Actions)")
        elif val != val.strip() or "\n" in val or " " in val:
            notes.append(f"{name}: contains a space or line break. Re-add the secret without it")
    if url and not re.fullmatch(r"https://[a-z0-9]+\.supabase\.co/?", url.strip()):
        notes.append("SUPABASE_URL: does not look like https://<project>.supabase.co "
                     "(use the Project URL from Supabase, Project Settings, API)")
    if key:
        kind = ("new secret key (sb_secret_)" if key.strip().startswith("sb_secret_")
                else "PUBLISHABLE key (sb_publishable_), wrong: use the secret key" if key.strip().startswith("sb_publishable_")
                else "legacy JWT key" if key.strip().count(".") == 2 else "unrecognised format")
        notes.append(f"SUPABASE_KEY: {kind}, {len(key.strip())} characters")
    return notes


def cmd_db_check(_args) -> int:
    """Test the Supabase connection, the key, and every table. Result goes to the run page and Telegram."""
    from . import owner_feed
    from .config import MissingSecret
    from .store import Store

    pre = _db_preflight()
    try:
        store = Store()
    except MissingSecret as exc:
        lines = ["DB check: FAILED, " + str(exc)] + ["  " + n for n in pre]
        results = []
    else:
        results = store.check()
        bad = [r for r in results if not r[1]]
        key_kind = "apikey header only" if store.headers.get("Authorization") is None else "apikey + Bearer"
        lines = [f"DB check: {'ALL OK' if not bad else str(len(bad)) + ' of ' + str(len(results)) + ' tables FAILED'} ({key_kind})"]
        lines += ["  " + n for n in pre]
        lines += [f"  {t}: {'ok' if ok else 'FAILED ' + d}" for t, ok, d in results]
        if bad:
            lines.append("Fix: 403 / 42501 permission denied = run db/grant_service_role.sql in the Supabase SQL editor; "
                         "401 = wrong or mistyped SUPABASE_KEY; 404 = table missing, run db/schema.sql; "
                         "no response or HTML = wrong SUPABASE_URL.")
    failed = lines[0].startswith("DB check: FAILED") or any(not r[1] for r in results)
    print("\n".join(lines))
    if os.environ.get("GITHUB_ACTIONS"):  # make the reason visible on the run page itself
        for t, ok, d in results:
            if not ok:
                print(f"::error title=db-check {t}::{d}")
        for n in pre:
            if "NOT SET" in n or "space" in n or "does not look" in n or "wrong" in n:
                print(f"::error title=db-check secret::{n}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write("### DB check\n\n```\n" + "\n".join(lines) + "\n```\n")
    owner_feed.message("\n".join(lines))
    return 1 if failed else 0


def cmd_token_issued(args) -> int:
    """Record when an Instagram/Threads token was really generated (the token cannot tell us itself)."""
    from datetime import datetime, timedelta, timezone

    from .store import Store

    when = datetime.now(timezone.utc) - timedelta(days=args.days_ago)
    Store().record_token_refresh(args.platform, when)
    print(f"recorded {args.platform} token issue date: {when.date()} ({args.days_ago} days ago)")
    return 0


def cmd_notify(args) -> int:
    """Send a free-form message to the owner's private Telegram chat (used by workflow failure steps)."""
    from . import owner_feed
    owner_feed.message(" ".join(args.text))
    return 0


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
    p = sub.add_parser("test-post")
    p.add_argument("platform", choices=["facebook", "instagram", "threads", "all"])
    p.add_argument("--live", action="store_true", help="really publish (default is a dry run)")
    p.add_argument("--image-url", default="", help="public JPEG URL (required for instagram)")
    p.add_argument("--keep", action="store_true", help="do not delete the Facebook test post")
    p.set_defaults(fn=cmd_test_post)
    sub.add_parser("db-check").set_defaults(fn=cmd_db_check)
    p = sub.add_parser("token-issued")
    p.add_argument("platform", choices=["instagram", "threads"])
    p.add_argument("--days-ago", type=int, default=0)
    p.set_defaults(fn=cmd_token_issued)
    p = sub.add_parser("notify")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_notify)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
