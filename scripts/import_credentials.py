#!/usr/bin/env python3
"""Map a loosely labelled credentials text file to the canonical secret names, without printing values.

  python scripts/import_credentials.py path/to/file.txt                 -> writes .env (chmod 600), reports names
  python scripts/import_credentials.py file.txt --gh-secrets OWNER/REPO -> also sets GitHub Actions secrets (needs gh auth)

Labels in the file ("Insta Access token", "Thread App Secret", ...) are matched case-insensitively,
ignoring spaces and punctuation. See docs/meta-setup.md section 8 for the canonical names.
"""
import argparse
import os
import re
import subprocess
import sys

ALIASES = {
    "FB_APP_ID": ["appid", "fbappid", "facebookappid", "mainappid"],
    "FB_APP_SECRET": ["appsecret", "fbappsecret", "facebookappsecret", "mainappsecret"],
    "FB_PAGE_ID": ["fbpageid", "pageid", "facebookpageid"],
    "FB_PAGE_TOKEN": ["fbpagetoken", "pagetoken", "pageaccesstoken", "facebookpagetoken"],
    "IG_APP_ID": ["igappid", "instagramappid", "instaappid"],
    "IG_APP_SECRET": ["igappsecret", "instagramappsecret", "instaappsecret"],
    "IG_USER_ID": ["iguserid", "instagramuserid", "instauserid"],
    "IG_TOKEN": ["igtoken", "instagramaccesstoken", "instaaccesstoken", "instagramtoken", "instatoken"],
    "TH_APP_ID": ["thappid", "threadappid", "threadsappid"],
    "TH_APP_SECRET": ["thappsecret", "threadappsecret", "threadsappsecret"],
    "TH_USER_ID": ["thuserid", "threaduserid", "threadsuserid"],
    "TH_TOKEN": ["thtoken", "threadaccesstoken", "threadsaccesstoken", "threadtoken", "threadstoken"],
    "TELEGRAM_BOT_TOKEN": ["telegrambottoken", "telegramapi", "telegrambotapi", "telegramtoken"],
    "TELEGRAM_OWNER_ID": ["telegramownerid", "telegramowner"],
    "TELEGRAM_CHANNEL_ID": ["telegramchannelid", "telegramchannel"],
    "SUPABASE_URL": ["supabaseurl"],
    "SUPABASE_KEY": ["supabasekey", "supabasesecretkey", "supabaseservicekey", "supabaseservicerolekey"],
    "GROQ_API_KEY": ["groqapikey", "groqapi", "groqkey"],
    "GEMINI_API_KEY": ["geminiapikey", "geminiapi", "geminikey"],
}
# Not stored: short-lived or user-level tokens are only stepping stones to the Page token.
IGNORED = {"shorttoken", "newaccesstoken", "longlivedusertoken", "usertoken"}
CANON_ORDER = list(ALIASES)
LOOKUP = {a: canon for canon, al in ALIASES.items() for a in al + [re.sub(r"\W", "", canon.lower())]}
HEADER = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*:\s*$")
LINE = re.compile(r"^\s*([^:\t=]+?)\s*(?:[:=]|\t+)\s*(\S.*?)\s*$")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse(text):
    found, conflicts, ignored, unlabelled = {}, set(), [], 0
    section = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        h = HEADER.match(line)
        if h:
            section = norm(h.group(1))
            continue
        m = LINE.match(line)
        if not m:
            if len(line.strip()) > 40 and " " not in line.strip():
                unlabelled += 1  # a bare token on its own line
            continue
        key, val = norm(m.group(1)), m.group(2)
        if section and section + key in LOOKUP:
            key = section + key
        if key in IGNORED:
            ignored.append(m.group(1).strip())
        elif key in LOOKUP:
            canon = LOOKUP[key]
            if canon in found and found[canon] != val:
                conflicts.add(canon)
            found.setdefault(canon, val)
    return found, conflicts, ignored, unlabelled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--out", default=".env")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output file")
    ap.add_argument("--gh-secrets", metavar="OWNER/REPO")
    a = ap.parse_args()
    if os.path.exists(a.out) and not a.force:
        sys.exit(f"{a.out} already exists; pass --force to overwrite or --out to write elsewhere")
    found, conflicts, ignored, unlabelled = parse(open(a.file, encoding="utf-8").read())

    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        for name in CANON_ORDER:
            f.write(f"{name}={found.get(name, '')}\n")
    print(f"wrote {a.out} (mode 600, gitignored)")
    print("SET    :", ", ".join(n for n in CANON_ORDER if n in found) or "-")
    print("MISSING:", ", ".join(n for n in CANON_ORDER if n not in found) or "-")
    if conflicts:
        print("CONFLICT (same name, different values, first kept):", ", ".join(sorted(conflicts)))
    if ignored:
        print("IGNORED (short-lived/user tokens, not stored):", ", ".join(ignored))
    if unlabelled:
        print(f"IGNORED: {unlabelled} unlabelled token-like line(s). Revoke any token you no longer need.")

    if a.gh_secrets:
        for name in CANON_ORDER:
            if name in found:
                subprocess.run(["gh", "secret", "set", name, "--repo", a.gh_secrets],
                               input=found[name], text=True, check=True, stdout=subprocess.DEVNULL)
        print(f"set {len(found)} GitHub Actions secrets on {a.gh_secrets}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
