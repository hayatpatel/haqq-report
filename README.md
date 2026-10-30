# Haqq Report

An independent, automated news desk for reporting across the Muslim world and global crisis zones.
Palestine first, then Sudan, Yemen, Syria, Kashmir and other under-reported emergencies, plus
Islamophobia and evidence-based fact-checking.

**Facts first. Sources named. Corrections public.**

This README is the single source of truth for the project. Longer background lives in [`docs/`](docs/).

---

## Editorial standards (enforced in code where possible)

1. **Truth beats speed.** One false viral post destroys years of trust.
2. **Named attribution on every claim.** "The Gaza Health Ministry said", "UN OCHA reported". A casualty figure without its named source fails the safety check.
3. **Zero hate.** Criticise governments, armies and policies. Never attack a people or a religion.
4. **Risky content is flagged.** Child victims, graphic content, genocide or war-crime wording, unverified viral clips, sectarian topics, designated groups and calls to action are classed **Red**. With the current setting (`red_needs_approval: false`) Red posts go out like the rest, with "Flagged: ..." in your Telegram message so you can review and delete them first. Set `red_needs_approval` back to `true` to make Red wait for you.
5. **The AI never gives religious rulings.** No fatwas, tafsir or its own interpretation.
6. **The desk never says "genocide", "ethnic cleansing" or "war crime" in its own voice.** It quotes the named body that said so (ICJ, UN commissions, Amnesty, HRW, B'Tselem and others).
7. **Public corrections log** for every update or retraction.
8. **Images are honest.** Licensed photos only (credited), recent photos for war zones, and anything AI-made is labelled "AI-generated illustration" on the image and in the caption.

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
| Green | Occasions, Myth vs Fact with a fact-check source, UN data, positive stories | Posts automatically |
| Amber | Casualty updates, hate-crime reports, court rulings, politics | Two sources or Tier A, extra checks, attribution |
| Red | Child victims, graphic content, genocide wording, detainees/hostages, unverified viral video, sectarian topics, designated groups, calls to action, Kashmir/Xinjiang political claims | Flagged in Telegram. Waits for approval only if `red_needs_approval` is `true` |

Dignity rules: no images of bodies, no identifiable children, victims named only when family or officials released the name. Classification and post-draft checks are in [`newsbot/safety.py`](newsbot/safety.py).

## How it works

```
sources (feeds, Google News searches, creators) -> ingest, dedupe, 48h window
  -> claims + tier -> verify (one Tier A/B source is enough) -> score
  -> draft with named attribution -> safety checks (retry once, else skip) -> duplicate check
  -> per-platform wording (variants.py) -> photo or AI illustration -> feed card + story card
  -> publish: Facebook, Instagram, Threads (+ Facebook and Instagram stories)
  -> private Telegram message with links, errors and the image outcome
```

Two paths feed the same code (`runner.handle_article`):

- **Full run** (`ingest.yml`, every 4 hours, started by the listener if GitHub skips it): picks up to `run.max_articles` stories, reserves one slot each for Palestine and Islamophobia (`pillars`), posts at most `run.max_posts_per_run` (2) per run, up to `limits.MAX_POSTS_PER_DAY` (10) a day.
- **Fast track** (inside the always-on listener, every 5 minutes): critical news (severity 2 to 3) and celebrity events are posted ahead of the queue. See below.

Platforms: Facebook Page, Instagram, Threads, each with its own credentials and code path, so one failing never stops the others. Telegram channel, Bluesky and the website exist in code but are switched off in `config.json`.

## Current status

Live in `auto` mode on Facebook, Instagram and Threads, with Facebook and Instagram stories. Details of what was planned versus built: [`docs/status.md`](docs/status.md) and [`docs/project-plan.md`](docs/project-plan.md).

| Area | State |
|---|---|
| Pipeline, safety lanes, verification, scoring, drafting (Groq then Gemini) | Live |
| Facebook, Instagram, Threads posting; stories on Facebook and Instagram | Live |
| Fast track, celebrity once-a-day rule, Islamophobia pillar | Live |
| Photos (Wikimedia Commons), photo cards, AI illustration fallback | Live |
| Per-platform wording, daily digest, weekly reach report | Live (weekly report needs a week of data) |
| Telegram control: listener, `/pause`, `/fbtoken`, `/igtoken`, `/igsetup` and more | Live |
| Telegram approval buttons, Cloudflare Worker (`worker/`) | Not used (`approvals.buttons: false`) |
| Telegram channel, Bluesky, website | Built, switched off |

## Repository layout

```
newsbot/
  runner.py        full run, auto-posting, caps, retries, story-only path, catch-up of missed platforms
  pipeline.py      claims, verify, draft, checks, publish_draft (per-platform posts, stories, cooldown)
  fasttrack.py severity.py celebrity.py islamophobia.py   fast track, celebrity and Islamophobia rules
  variants.py      per-platform wording with code checks
  template.py      caption template, hashtags, length fitting
  photos.py ai_art.py cards.py   licensed photos, AI illustration fallback, feed and story cards
  duplicates.py cleanup.py       duplicate guard, 72h retention
  insights.py digest.py          engagement reads, weekly report, daily digest
  approvals.py     Telegram listener and commands, catch-up of skipped runs
  fbtoken.py socialtoken.py      Facebook, Instagram and Threads token tools
  ratelimit.py     cooldown after Meta throttling
  owner_feed.py runlog.py        Telegram messages and run logs
  config.py models.py sources.py ingest.py claims.py verify.py safety.py scoring.py
  factcheck.py calendar_islam.py corrections.py health.py ai.py store.py
  publish/         base.py (retry, isolation, pause), meta_fb.py, instagram.py, threads.py, tokens.py,
                   bluesky.py, telegram_channel.py, site.py
db/                schema.sql, grant_service_role.sql, migrations/ (002 to 005)
data/              sources.seed.json (the source registry)
docs/              meta-setup.md, project-plan.md, status.md
scripts/           set_github_secrets.sh, import_credentials.py, resolve_ids.py
tests/             pytest suite
.github/workflows/ see "GitHub Actions"
config.json        every non-secret setting (see "Configuration")
```

## Quick start (local)

```bash
git clone https://github.com/hayatpatel/haqq-report && cd haqq-report
pip install -r requirements-dev.txt
pytest                              # run the test suite
cp .env.example .env                # fill in locally; .env is gitignored, never commit it
python -m newsbot check-config      # which secrets are set (names only, never values)
python -m newsbot ingest            # fetch feeds and print a summary
python -m newsbot run               # full cycle (needs Supabase and AI keys; posts if mode is auto and platforms are enabled)
```

Other commands: `calendar`, `corrections`, `health`, `listen --minutes N`, `cleanup`, `card-test`, `test-post`, `db-check`, `ai-check`, `token-issued`, `notify`.

## Secrets

Never committed. Locally in `.env`; in production as GitHub Actions secrets. [`.env.example`](.env.example) lists every name. Each platform module reads only its own credentials.

| Platform | Secrets |
|---|---|
| Facebook | `FB_APP_ID`, `FB_APP_SECRET`, `FB_PAGE_ID`, `FB_PAGE_TOKEN` (Page token, never expires) |
| Instagram | `IG_APP_ID`, `IG_APP_SECRET`, `IG_USER_ID`, `IG_TOKEN` (60 days, auto-refreshed). Optional never-expiring route through the Facebook Page token: `/igsetup` |
| Threads | `TH_APP_ID`, `TH_APP_SECRET`, `TH_USER_ID`, `TH_TOKEN` (60 days, auto-refreshed) |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID` (private chat). `TELEGRAM_CHANNEL_ID` and `TELEGRAM_WEBHOOK_SECRET` are optional and unused |
| Supabase | `SUPABASE_URL`, `SUPABASE_KEY` (a service-role key; `sb_secret_` keys go in the `apikey` header only) |
| AI drafting | `GROQ_API_KEY`, `GEMINI_API_KEY` |
| AI illustration (optional, free) | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` (Workers AI permission) |
| Token refresh by the health job | `GH_PAT_SECRETS` (fine-grained PAT, secrets:write on this repo only) |
| Bluesky (not planned) | `BSKY_HANDLE`, `BSKY_APP_PASSWORD` |

Repository variable `BOT_ENABLED=true` switches on all scheduled workflows. Tokens sent from Telegram (`/fbtoken`, `/igtoken`, `/thtoken`) and automatic refreshes are saved in the Supabase `settings` table and override the GitHub secret.

To upload a filled-in `.env` to GitHub: `gh auth login`, then `./scripts/set_github_secrets.sh`. To start from a loosely labelled credentials file: `python scripts/import_credentials.py FILE`, then `python scripts/resolve_ids.py`, then `./scripts/set_github_secrets.sh` (the importer prints names only, skips short-lived tokens and refuses to overwrite `.env` without `--force`). Keep credential files outside the repository and delete them afterwards. Meta setup is in [`docs/meta-setup.md`](docs/meta-setup.md). **Rotate any credential that was ever pasted into a chat, ticket or file.**

## GitHub Actions

Cron runs in UTC; all scheduled workflows do nothing unless the variable `BOT_ENABLED` is `true`. GitHub often skips scheduled runs on the free tier, so the listener compensates (see below).

| Workflow | When | Purpose |
|---|---|---|
| `ingest` | every 4 hours (`5 */4 * * *`) and started by the listener when overdue | the full run |
| `listen` | a new one queued every 5 minutes (only one waits); each stays up 3 h 50 min, or ends itself within 5 minutes of a new push to main so the new code starts by itself | Telegram commands in seconds, fast track every 5 minutes, catch-up of skipped runs |
| `health` | daily 18:10 IST | token check, expiry alert 10 days ahead |
| `calendar` | daily 05:00 IST | Islamic occasions, election-silence reminders |
| `keepalive` | 1st and 15th | keeps the free Supabase project awake |
| `tests` | every push | pytest |
| `card-test` | manual | renders and uploads a sample feed and story card, sends them to Telegram |
| `test-post` | manual | harmless test post to one platform (dry run unless *live*) |
| `db-check`, `ai-check` | manual | database access check; which AI models your keys can use |
| `approvals` | manual only | one-off Telegram poll; do not run while the listener is active |
| `breaking` | schedule off | superseded by the fast track |

The listener has `actions: write` so it can start `ingest.yml` when no full run has begun for `run.catchup_hours` (4.7). Start the listener once by hand after deploying (Actions, **listen**, Run workflow); a new one starts to replace it before it ends. New code is picked up automatically: the running listener notices the push, ends, and the queued one starts with it. No manual cancel is needed.

## Telegram (private owner chat)

Every message goes to your own chat with the bot. Post message: status line (✅ posted, ⚠️ partly posted, ❌ not posted), headline, one line per platform with a link, a plain-words "Fix:" if something failed, the source, and `Image:` lines (photo used, wording tailored for which platforms, or why an image step failed). Also: daily digest (08:00 IST), weekly reach report (Monday 09:00 IST), daily health report (18:10 IST), run logs, and `ERROR: workflow ... failed` with a link.

| Command | What it does |
|---|---|
| `/status` | what is running, caps, pause and fast-track state |
| `/pause`, `/resume` | stop or restart all posting (drafts wait, nothing is lost) |
| `/fast on` or `off` | the fast track |
| `/celeb list`, `add NAME`, `remove NAME` | the celebrity watchlist |
| `/fbtoken <token>` | check and save a Facebook token (turned into the never-expiring Page token); `reset` returns to the secret |
| `/igtoken <token>`, `/thtoken <token>` | same for Instagram and Threads (exchanged for 60-day tokens) |
| `/igsetup` | switch Instagram to the never-expiring Facebook Page route; `/igsetup login` goes back |
| `/tokenstatus` | asks Facebook, Instagram and Threads whether each token works now, with the exact error |
| `/help` | list of commands |

Only the owner's private chat is obeyed. Messages that contain a token are deleted after processing. `owner_feed` in `config.json` switches message types and the run-log policy.

## Posts, photos and stories

**One post per platform** (`variants.py`, `template.py`, `platform_style`): after the draft passes every safety check, one extra AI call rewrites it separately for Facebook, Instagram and Threads using only the draft's facts. Code rejects a rewrite that adds a number, link, emoji or hashtag, uses engagement bait ("like if", "tag a friend"), or loses the on-ground label; that platform then uses the standard text.

| Platform | Style |
|---|---|
| Facebook | headline, two short paragraphs, optional open question, 3 hashtags; source link in the first comment (link posts reach less; if the token lacks `pages_manage_engagement` the link goes into the caption); 4:5 image |
| Instagram | first line is a hook under about 120 characters (the part visible before "more"), short lines, 5 hashtags, "Save this post for reference.", no links; 4:5 image |
| Threads | short and conversational (about 300 characters), one topic tag, short outlet link when it fits |

Every caption is fitted to its limit (`caption_limits`; Threads 480) while keeping the source line and photo credit. The real outlet is named even for Google News links. The kicker is **BREAKING:** or **UPDATE:** when it applies.

**Photos** (`photos.py`): Wikimedia Commons only; public domain, CC0 and CC BY only (no share-alike, non-commercial or no-derivatives); a named author is required; no graphic or child content; minimum size from `photos.min_width` and `photos.min_height`. The caption says `Representative photo: <author> / Wikimedia Commons, <licence>`. For war zones (Gaza, Sudan, Yemen, Syria) only photos of destruction taken in `photos.war_min_year` (2024) or later qualify, so an undamaged skyline never illustrates a catastrophe.

**No photo found:** `ai_art.py` makes an abstract symbolic illustration with free Cloudflare Workers AI (FLUX.1 schnell): no people, real places, flags or text, labelled "AI-GENERATED ILLUSTRATION" on the image and in the caption. Own budget `ai_art.max_per_day` (40); when Cloudflare's free allowance runs out the bot stops until the next day and uses the plain brand card. Stay on the Cloudflare Free plan so nothing can be charged.

**Cards** (`cards.py`): feed card 1080x1350 (4:5) and story card 1080x1920 (9:16): darkened photo background, brand and date, topic badge, kicker, headline, `#HaqqReport`; stories print the credit on the image. Uploaded to the public Supabase Storage bucket `cards` (`db/migrations/004_cards_bucket.sql` creates it by hand if needed). If the feed card cannot be made, Facebook and Threads post text only and Instagram is skipped.

**Stories** (`enable.facebook_story`, `enable.instagram_story`): one story per post on each platform. If the daily cap or per-run cap blocks the feed post, the story still goes out once (`stories.max_per_day`, 30) and the draft stays queued. `/pause` stops stories too.

Run **card-test** to see the card, story and caption in Telegram.

## Fast track, celebrities, Islamophobia

- **Fast track** (`fasttrack.py`, `severity.py`; `fast_track` in config): every 5 minutes inside the listener, reads the fast sources (`"fast": true` in `data/sources.seed.json`) and Google News searches, keeps stories under 3 hours old that are on mission and severity 2 or 3 (mass casualties, ceasefire changes, ICJ/ICC rulings, famine declarations, attacks on mosques or protected sites), and posts the best 2 per cycle, at most 6 a day, ahead of the queue (extra `extra_daily_posts` 4 above the daily cap). Severity 3 carries BREAKING. One trusted Tier A/B source is enough.
- **Celebrities** (`celebrity.py`; `celebrity` in config, edit with `/celeb`): the headline must name the person and the event must be notable (match, award, signing, statement, charity) from a trusted outlet; gossip, relationships and health rumours are blocked. **One post per person per day (IST)** combining every outlet's report; a second post labelled UPDATE only for critical news, once a day. The day lock is a database row.
- **Islamophobia Watch** (`islamophobia.py`): Google News searches plus CAIR and Tell MAMA (labelled "reported by", Tier C). One slot per run is reserved. Drafting rules: what happened, where, who reported it; no motive guessing, no suspect description unless police or a court gave it, no generalising about a community.

## Reliability

- **No duplicates:** a URL is processed once; each article, draft and celebrity day is claimed atomically in the database; a draft with a headline similar to any from the last 72 hours is skipped; a unique index allows one post per platform per draft (`db/migrations/005_posts_unique.sql`).
- **Freshness and cleanup:** only stories from the last `INGEST_MAX_AGE_HOURS` (48) are posted; articles, claims, drafts and posts older than `retention.hours` (72) are deleted on every run, run logs after 7 days, engagement records after 60 days (`python -m newsbot cleanup` runs it by hand).
- **Platform isolation and pause:** each platform runs on its own with one retry. After 3 failures (`PLATFORM_FAILURES_BEFORE_PAUSE`) it is paused for `PLATFORM_PAUSE_MINUTES` (30) and Telegram shows the last real error.
- **Rate limits:** Meta throttle errors (codes 4, 17, 32, 613, "request limit") never pause a platform. A cooldown of `rate_limit_cooldown_minutes` (60) is stored in Supabase and respected by every workflow. Engagement reads are limited to `insights.max_calls_per_run` (20) and each post is read at most twice, because small Instagram accounts have a small call allowance.
- **Catch-up:** `retry_unposted` retries drafts that reached no platform; `retry_missing_platforms` posts to the platforms a published draft missed (24 hours, stories 6 hours, 3 tries).
- **Skipped GitHub schedules:** the listener starts the full run itself when none has begun for 4.7 hours.
- **Tokens:** Facebook Page token never expires; Instagram and Threads tokens are refreshed every 20 days by the bot (saved in Supabase); `/igsetup` makes Instagram never expire; `/tokenstatus` shows the live state.
- **Database down:** nothing is posted (the daily cap cannot be checked).

## Reach

`insights.py` reads engagement per post (Facebook reactions, comments, shares; Threads likes, replies, reposts; Instagram likes, comments, saves, shares) and keeps a small record for 60 days. The Monday report shows the best posting hours, topics and platforms and the top posts; use it to move the cron times in `ingest.yml`. Instagram and Threads insights need the insights permission on their tokens. The daily digest shows posts per platform, stories, full runs of the 6 expected, queue size and warnings.

## Configuration (`config.json`)

| Key | What it controls |
|---|---|
| `mode` | `auto` posts by itself; `review` only previews in Telegram |
| `red_needs_approval` | `false`: Red posts too (flagged); `true`: Red waits |
| `enable` | per platform on/off, including `facebook_story` and `instagram_story` |
| `limits` | `MAX_POSTS_PER_DAY` 10, `INGEST_MAX_AGE_HOURS` 48, failure pause and cooldown minutes, minimum Palestine posts |
| `run` | `max_articles`, `max_per_source`, `off_mission_max`, `max_posts_per_run`, `dedupe_days`, `catchup_hours` |
| `pillars` | reserved slots per run for Palestine and Islamophobia |
| `fast_track`, `celebrity` | fast track limits; celebrity watchlist and rules |
| `platform_style`, `caption_limits`, `hashtags`, `source_names`, `brand` | per-platform wording and hashtag counts, length limits, tags, outlet names, card branding |
| `photos`, `ai_art`, `cards`, `stories` | photo rules, AI illustration budget, cards on/off, story cap |
| `insights`, `digest`, `owner_feed`, `approvals` | engagement reads, digest hour, Telegram message switches, approval buttons (off) |
| `retention` | cleanup ages |
| `ai` | provider order (Groq, Gemini) and model lists; unavailable models are skipped |
| `watch_topics`, `mission_keywords`, `designated_group_keywords`, `trusted_publishers`, `scoring` | what counts as on mission and how stories rank |
| `meta` | Graph API versions and token lifetimes |
| `election_silence`, `schedule_ist`, `site` | blackout dates, schedule notes, website base URL |

## Setting up from scratch

1. Merge to main, add the secrets above and the variable `BOT_ENABLED=false`.
2. Supabase: run `db/schema.sql` (it ends with the `service_role` grants), then `db/migrations/003_articles_processed.sql` and `005_posts_unique.sql` (`002` and `004` are optional). Run **db-check**.
3. Run **health** (three platform lines on Telegram), **ai-check** and **card-test**.
4. Run **test-post** for each platform (dry run, then *live*). Facebook test posts are deleted again; Instagram and Threads test posts must be removed by hand.
5. Set `BOT_ENABLED=true`, start **listen** once, send `/status`, then `/tokenstatus` and `/igsetup`.
6. Watch the first posts in Telegram. Pause any time with `/pause` or by setting `BOT_ENABLED` to `false`. To go back to previews only, set `mode` to `review`.

## Troubleshooting

| Telegram says | Meaning and fix |
|---|---|
| Token is expired or invalid | Create a new token; send `/fbtoken`, `/igtoken` or `/thtoken <token>`; check with `/tokenstatus` |
| Access token lacks a permission (#200) | Regenerate with the needed permissions (Facebook `pages_manage_posts`, `pages_manage_engagement`; Instagram `instagram_business_content_publish`; Threads `threads_content_publish`) |
| The platform is limiting how often we can call it | Nothing to do: a 60-minute cooldown, then the post is retried |
| Paused after repeated failures. Last error: ... | Fix the named error; the platform retries after 30 minutes |
| No image was available for this post | Run **card-test**; check the `cards` bucket and Supabase key |
| `Image:` line says "none found" or "AI illustration not set up" | No licensed photo matched (normal for Gaza after 2024 filter); add the Cloudflare secrets for illustrations |
| Nothing responds to commands | No listener is running: Actions, **listen**, Run workflow |

## Break-glass

If the Meta app itself is restricted, all three Meta platforms stop at once. Recovery steps (create `HR-Publisher-2`, re-add use cases and testers, swap secrets) are in [`docs/meta-setup.md`](docs/meta-setup.md). Screenshot every restriction notice and keep an appeals log.

## Reporting a security problem

Do not open a public issue for a leaked credential or vulnerability. Contact the maintainer directly and rotate the affected secret immediately.
