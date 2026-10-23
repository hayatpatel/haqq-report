# Haqq Report

An independent, automated news desk for reporting across the Muslim world and global crisis zones.
Palestine first, then Sudan, Yemen, Syria, Kashmir and other under-reported emergencies, plus
Islamophobia and evidence-based fact-checking.

**Facts first. Sources named. Corrections public.**

This README is the single source of truth for the project. Longer background lives in [`docs/`](docs/).

---

## Editorial standards (these are enforced in code, not just written down)

1. **Truth beats speed.** One false viral post destroys years of trust.
2. **Named attribution on every claim.** "The Gaza Health Ministry said", "UN OCHA reported",
   "journalist X on the ground reported". No casualty figure without its named source.
3. **Zero hate.** Criticise governments, armies and policies. Never attack a people or a religion.
4. **Humans approve the risky stuff.** Child victims, graphic content, genocide or war-crime wording,
   unverified viral clips, sectarian topics, designated groups and calls to action never auto-post.
5. **The AI never gives religious rulings.** Verified Quran translations and graded hadith only. No
   fatwas, tafsir or its own interpretation.
6. **The desk never says "genocide", "ethnic cleansing" or "war crime" in its own voice.** It quotes the
   named body that said so (ICJ, UN commissions, Amnesty, HRW, B'Tselem and others).
7. **Public corrections log** for every update or retraction.

## Source tiers

| Tier | Who | Can post alone? |
|---|---|---|
| A | UN agencies, ICJ/ICC, court rulings, official statements, Gaza Health Ministry (always named) | Yes, with attribution |
| B | Established newsrooms (state-funded outlets labelled honestly) | Yes, with attribution |
| C | Verified independent creators and journalists | Only as a labelled "On-ground report: reported by NAME, not independently confirmed yet" until Tier A/B confirms |
| D | Viral social media | Never alone; needs verification |

A Tier C/D claim is upgraded when **any one** holds: Tier A confirms it; two independent Tier B/C
reporters on the ground agree; a verification group (Bellingcat, Forensic Architecture, AFP Fact Check,
BBC Verify) geolocated and dated the media; or the creator's score is 9+ and the claim is low-risk.
Automatic rejection: video older than the event, AI-generated image, photo from another war, screenshot
with no original link. Rules live in [`newsbot/verify.py`](newsbot/verify.py).

## Safety lanes

| Lane | Examples | Rule |
|---|---|---|
| Green | Occasions, Myth vs Fact with a fact-check source, UN data, positive stories | Auto post (after review period) |
| Amber | Casualty updates, hate-crime reports, court rulings, politics | Two sources or Tier A, extra checks, attribution, no photos of people |
| Red | Child victims, graphic content, genocide wording, detainees/hostages, unverified viral video, sectarian topics, designated groups, calls to action, Kashmir/Xinjiang political claims | Never auto-posts. Telegram approve/reject with draft and sources |

Dignity rules: no images of bodies, no identifiable children, victims named only when family or officials
released the name, content warnings where distressing. Classification and post-draft checks are in
[`newsbot/safety.py`](newsbot/safety.py).

## Pipeline

```
collect feeds + creators -> ingest, dedupe, 48h cap, translate -> extract claims + tag tier
  -> score (significance, impact, interest, gap bonus, watch-topic bonus)
  -> verified enough? (no: hold in watch list)
  -> classify Green / Amber / Red (Red: Telegram approval)
  -> draft with attribution -> checks pass? (retry once, then skip)
  -> visual card (no graphic images) -> publish to each enabled platform + website
  -> monitor comments and corrections
```

Platforms: Facebook Page, Instagram, Threads (all via one Meta app, each with its own credentials,
token and code path), plus Telegram channel, Bluesky and the website. Meta over-restricts Palestine
content, so no single platform is critical.

## Status

This repository is the **project scaffold**. Honest status of each part:

| Area | State |
|---|---|
| Config, safety lanes, verification tiers, scoring, claims, fact-check rules | Built and unit-tested |
| Meta publishers (Facebook, Instagram, Threads) with isolation, retry, auto-pause | Built, unit-tested with mocks. **Not yet exercised against the live Meta API** |
| Token health job (Facebook `debug_token`, Instagram/Threads age and refresh) | Built. Needs Supabase `tokens` rows and `GH_PAT_SECRETS` |
| Telegram channel, Bluesky, website generator, corrections log | Built, minimal |
| Telegram control bot (Cloudflare Worker) | Written, **not deployed** |
| Supabase schema | Written, **not applied** |
| Visual cards (`cards.py`), photos, weekly report, Palestine daily brief, creator vetting | Not built yet |
| GitHub Actions schedules | Present but **off** until `BOT_ENABLED=true` |

Everything ships **disabled and in review mode**: all platform flags in `config.json` are `false`, and
`mode` is `review` (every draft needs approval for the first 4 weeks).

## Repository layout

```
newsbot/
  config.py          config.json + environment secrets, redaction
  models.py          Article, Claim, Draft, Tier, Lane
  sources.py         source registry and tiers        (data/sources.seed.json)
  ingest.py          fetch, dedupe, 48h window
  claims.py          claim extraction + anti-hallucination trace check
  verify.py          tier and verification rules
  safety.py          lanes, language policy, number checks
  scoring.py         story scoring
  pipeline.py        orchestration (dry-run by default)
  factcheck.py       Myth vs Fact rules
  calendar_islam.py  occasions via AlAdhan, election-silence reminders
  corrections.py     public corrections log
  health.py          daily token health job
  ai.py              Groq -> Gemini failover
  store.py           Supabase client
  publish/
    base.py          retry, isolation, auto-pause, secret-safe errors
    meta_fb.py       Facebook Page      (FB_PAGE_ID, FB_PAGE_TOKEN)
    instagram.py     Instagram          (IG_USER_ID, IG_TOKEN)
    threads.py       Threads            (TH_USER_ID, TH_TOKEN)
    tokens.py        expiry check and refresh for IG and Threads
    bluesky.py  telegram_channel.py  site.py
worker/              Cloudflare Worker for the Telegram webhook
db/schema.sql        Supabase tables (RLS on, no public policies)
site/                static website output (generated)
data/                source registry seed
docs/                meta-setup.md, project-plan.md
scripts/             set_github_secrets.sh, import_credentials.py, resolve_ids.py
tests/               pytest suite
.github/workflows/   tests, ingest, breaking, calendar, health, keepalive
config.json          non-secret settings (modes, limits, watch topics, schedule)
```

## Quick start

```bash
git clone https://github.com/hayatpatel/haqq-report && cd haqq-report
pip install -r requirements-dev.txt
pytest                              # run the test suite
cp .env.example .env                # fill in locally; .env is gitignored, never commit it
python -m newsbot check-config      # shows which secrets are set (names only, never values)
python -m newsbot ingest            # fetch feeds and print a summary
python -m newsbot calendar          # upcoming Islamic occasions and election-silence reminders
python -m newsbot corrections       # regenerate the public corrections page
python -m newsbot health            # token health (needs credentials)
```

## Secrets

Secrets are **never** committed. Locally they live in `.env` (gitignored); in production they are GitHub
Actions secrets, and the Telegram Worker uses Cloudflare secrets. [`.env.example`](.env.example) lists every
name. Each platform module reads only its own credentials, so a problem on one platform cannot leak into
or break another.

| Platform | Secrets |
|---|---|
| Facebook | `FB_APP_ID`, `FB_APP_SECRET`, `FB_PAGE_ID`, `FB_PAGE_TOKEN` (Page token, never expires) |
| Instagram | `IG_APP_ID`, `IG_APP_SECRET`, `IG_USER_ID`, `IG_TOKEN` (60 days, refreshable; Instagram's own ID/secret, not the main app's) |
| Threads | `TH_APP_ID`, `TH_APP_SECRET`, `TH_USER_ID`, `TH_TOKEN` (60 days, refreshable; Threads' own ID/secret) |
| Token refresh | `GH_PAT_SECRETS` (fine-grained PAT, secrets:write on this repo only) |
| Telegram (private owner feed) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID`, `TELEGRAM_WEBHOOK_SECRET` (only for the approval Worker). `TELEGRAM_CHANNEL_ID` is **optional**: leave it empty unless you later turn on a public channel |
| Bluesky (**not planned**, leave empty) | `BSKY_HANDLE`, `BSKY_APP_PASSWORD`. The module stays in the code, disabled in `config.json` |
| Supabase | `SUPABASE_URL`, `SUPABASE_KEY` |
| AI | `GROQ_API_KEY`, `GEMINI_API_KEY` |

To upload a filled-in `.env` to GitHub Actions secrets: `gh auth login`, then `./scripts/set_github_secrets.sh`.

**Starting from a loosely labelled credentials file** (for example "Insta Access token", "Thread App Secret"):

```bash
python scripts/import_credentials.py path/to/credentials.txt   # writes .env with the canonical names above
set -a; . ./.env; set +a
python scripts/resolve_ids.py                                  # prints IG_USER_ID and TH_USER_ID (not secret); add them to .env
./scripts/set_github_secrets.sh
python -m newsbot check-config                                 # names only, never values
```

The importer prints which names were set or missing, never the values. It skips short-lived and user-level
tokens (they are only stepping stones to the Page token), refuses to overwrite an existing `.env` without
`--force`, and the credentials file itself must stay outside the repository. Delete it once the secrets are in
GitHub.

Meta setup (one app `HR-Publisher`, three use cases, no Page-to-Instagram link) is documented step by step in
[`docs/meta-setup.md`](docs/meta-setup.md). Rotate any credential that has ever been pasted into a chat,
ticket or file outside the secret stores.

## Telegram owner feed (private)

No public channel is needed. The bot sends everything to your own private chat with it
(`TELEGRAM_OWNER_ID`; start the chat once by sending the bot a message):

| Message | When | Switch in `config.json` (`owner_feed`) |
|---|---|---|
| Post summary: which platforms published (or would have, in a dry run) and which failed | Every post | `posts` |
| `ERROR in ...` with the detail | A pipeline error | `errors` |
| Daily token and platform health report | Daily 18:10 IST; a failure is always sent | `daily_health_report` |
| `ERROR: workflow '...' failed` with a link to the run | Any scheduled workflow fails | always on |

The feed never raises: if Telegram is down or unconfigured, publishing carries on. Weekly insights and a
per-post log in the database are still on the roadmap.

## Running the project (dry run)

`python -m newsbot run` (workflow **run (fetch, store, draft)**, hourly once `BOT_ENABLED=true`, or *Run workflow*
by hand) does a full cycle: fetch feeds, skip stories already stored, save articles, extract and verify claims,
draft with the AI, run the safety checks, save claims and drafts, and send each draft to your Telegram **for review**.
**Nothing is published.** Review mode, the disabled platform flags and a dry-run publisher all stop that, and
real posting stays off until the approval Worker and the visual cards exist.

Every run also leaves a detailed record:

| Where | What |
|---|---|
| `logs/<name>-<time>.log` | Full DEBUG log with every secret value removed. Uploaded as a downloadable **Actions artifact** (14 days) |
| Supabase `run_logs` | Status, summary counts and the log text (last 200 KB) |
| Telegram | The log file itself, sent to your private chat (`owner_feed.log_file`: `always`, `on_error` or `never`) |

AI models: `config.json` lists several candidates per provider and tries them in order, skipping any that return
400/403/404 (provider model names change; `gemini-2.0-flash` was shut down on 2026-06-01). Run the **ai-check** workflow
to see which models your keys can use and get a test result on Telegram. Articles are marked `processed` after
drafting (`db/migrations/003_articles_processed.sql`), so stories stored during an AI outage are retried.

Articles, claims and drafts are stored in the `articles`, `claims` and `drafts` tables. The `run_logs` table and
`articles.source_name` column come from `db/migrations/002_run_logs.sql` (also in `db/schema.sql`); run it once
in the Supabase SQL editor if you applied the schema before.

## Launch checklist

1. Merge the open pull requests, then add every secret (see Secrets) and the variable `BOT_ENABLED=false`.
2. Supabase: paste `db/schema.sql` into the SQL editor and run it. It ends with the `service_role` grants that Supabase
   now requires for new tables. If you ran an older copy and db-check says `42501 permission denied`, run
   `db/grant_service_role.sql` once. The `tokens` rows are then created by the health job.
3. Actions, **Daily health (tokens)**, *Run workflow*. Expect a Telegram report with three platform lines.
4. Actions, **test-post**, platform `facebook`, leave *live* unticked (dry run), then tick *live*. The post
   appears on the Page and is deleted again. Repeat for `threads` and `instagram` (needs a public JPEG URL).
   Instagram and Threads test posts cannot be deleted through the API: remove them by hand.
5. Turn platforms on one at a time in `config.json` (`enable`), keep `mode` at `review`, then set
   `BOT_ENABLED=true`. Run in review mode for at least 2 weeks before allowing automatic posting.

## GitHub Actions

Times are IST in the plan; cron runs in UTC.

| Workflow | When | Purpose |
|---|---|---|
| `tests` | every push and PR | pytest |
| `ingest` | hourly | collect, translate, score |
| `breaking` | every 15 min | priority (Palestine/crisis) feeds, event-driven lane |
| `calendar` | daily 05:00 IST | occasions and election-silence reminders |
| `health` | daily 18:10 IST | token check; alert 10 days before expiry |
| `keepalive` | 1st and 15th | keep the free Supabase project awake |

Every scheduled workflow is gated on the repository variable **`BOT_ENABLED`** and does nothing until you set it to
`true` (Settings, Secrets and variables, Actions, Variables). They can always be run by hand with *Run workflow*.

**Minutes warning:** `breaking` at 15-minute intervals is about 2,900 minutes a month, which exceeds the 2,000 free
minutes on a *private* repository. Either slow it to `*/30` or hourly, or make the repository public (unlimited minutes;
safe only because no secrets are stored in it). Check your Actions usage before enabling it.

## Go-live checklist

1. Apply [`db/schema.sql`](db/schema.sql) in Supabase; record token issue dates in the `tokens` table.
2. Add all secrets (see above). Add a second admin to the Page and the Meta app.
3. Test each platform separately with a harmless post; switch one off and confirm the others still post.
4. Deploy the Worker (`cd worker && npx wrangler deploy`), set its secrets, register the Telegram webhook with the secret token.
5. Lawyer review of the legal section of [`docs/project-plan.md`](docs/project-plan.md) (India BNS 196/299/353, IT Act; UK/EU/US defamation).
6. Two-week dry run in review mode, then soft launch at 3 to 4 posts a day. Switch to `auto` for Green and Amber only after 4 weeks; Red stays manual forever.

## Break-glass

If the Meta app itself is restricted, all three Meta platforms stop at once. Telegram, Bluesky and the website keep
the audience. Recovery steps (create `HR-Publisher-2`, re-add use cases and testers, swap secrets) are in
[`docs/meta-setup.md`](docs/meta-setup.md). Screenshot every restriction notice and keep an appeals log.

## Reporting a security problem

Do not open a public issue for a leaked credential or vulnerability. Contact the maintainer directly, and rotate the
affected secret immediately.


## Draft quality rules

- **What gets drafted:** each run picks on-mission stories first (`watch_topics` and `mission_keywords` in `config.json`), best source tier then newest, with at most `run.max_per_source` per source and `run.off_mission_max` off-topic stories.
- **Casualty figures** must name their source in the same sentence (OCHA, UNRWA, a ministry, a named outlet). "The agency said" fails the safety check.
- **Cleanup:** headlines are plain text (no markdown or quotes). Drafts about death, injury or disaster have all emoji removed.

## Going live (auto mode)

`mode` is `auto` in `config.json`. Each hourly run drafts stories, checks them, and then:

- **Green and Amber** stories post straight to Facebook and Threads, are recorded in `posts`, and appear in your Telegram feed with the platform post ids. Instagram stays off until image cards exist.
- **Red** stories (sensitive, designated-group mentions, graphic, and so on) are never auto-posted. They arrive in Telegram with **Approve and post** and **Reject** buttons, handled by the `approvals` workflow every 5 minutes.

Safeguards: `MAX_POSTS_PER_DAY` (10) is enforced; nothing posts if the database is unreachable (the cap could not be checked); every claim needs a source, casualty figures need a named source, and a failed safety check twice means the story is skipped.

Reviewing later: use Telegram or the platform itself. Delete a post on the platform and, if it was wrong, add a public correction (`corrections` workflow).

To pause: set the repository variable `BOT_ENABLED` to `false`, or set `mode` back to `review` in `config.json`.
