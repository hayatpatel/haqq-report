# Haqq Report: plan vs build (26 Oct 2026)

Reference: [project-plan.md](project-plan.md), [meta-setup.md](meta-setup.md), [README](../README.md) (the README wins on any difference).

| Plan item | Where | State |
|---|---|---|
| Source tiers A to D, single Tier A/B source can post | `verify.py`, `sources.py`, `data/sources.seed.json` | Built. 25 sources, 15 on the fast track |
| Mandatory attribution, casualty figure needs a named source, no guilt labels, no collective blame | `safety.py` | Built |
| Safety lanes Green / Amber / Red | `safety.classify_lane` | Built. Red posts directly (owner decision, `red_needs_approval: false`) and is flagged in Telegram |
| Duplicate protection | `duplicates.py`, article and draft claims in `store.py`, celebrity day lock | Built |
| Breaking lane (plan section 8) | `fasttrack.py`, `severity.py`, `listen.yml` | Built: checked every 5 minutes inside the always-on listener |
| Islamophobia Watch pillar (15%) | `islamophobia.py`, feeds (Google News searches, CAIR, Tell MAMA), reserved slot per run | Built |
| Celebrity coverage | `celebrity.py` | New scope, built with limits (watchlist, trusted outlets, no gossip, one post per day) |
| Corrections log | `corrections.py` | Built (command `/correct` not built) |
| Islamic calendar | `calendar_islam.py` | Built |
| Weekly insights report (KPIs, plan 2 row 14) | `insights.py` | Built |
| Telegram `/pause /resume /status` | `approvals.py` | Built. `/approve /reject /correct /source /creator /watch /brief` not built |
| Creator vetting, Myth vs Fact, Daily Palestine Brief, Weekly Ledger, translation, Bluesky, website, WhatsApp | | Not built yet |
| Cloudflare Worker webhook | `worker/` | Replaced by the always-on listener (`listen.yml`) |

## Oct 2026: photos and stories
Built: licensed Commons photos with printed/caption credit, photo-background feed and story cards, Facebook and Instagram stories for every post
(story-only when capped), image diagnostics in Telegram. Not yet verified live: Commons search (sandbox cannot reach it) and Supabase Storage upload.
