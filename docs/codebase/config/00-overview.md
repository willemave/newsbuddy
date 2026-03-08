# config/

Source folder: `config/`

## Purpose
File-backed feed and tooling configuration used by scraper bootstrapping, onboarding defaults, and size-guard tooling.

## Runtime behavior
- `app/utils/paths.py` resolves this folder by default and allows overrides via `NEWSAPP_CONFIG_DIR` plus per-file env vars.
- `app/scraping/runner.py` actively schedules Hacker News, Reddit, Substack, Techmeme, Podcasts, and Atom scrapers; Twitter and YouTube configs remain available for disabled or ad-hoc flows.
- Example files document expected shape for operators without forcing every deployment to commit secrets or local-only paths.

## Files
| File | What it controls | Current role |
|---|---|---|
| `config/substack.yml` | Curated Substack feeds (`url`, `name`, `limit`) | Default Substack inputs for onboarding/import flows; runtime subscriptions now primarily live in `user_scraper_configs`. |
| `config/substack.example.yml` | Example Substack feed file | Template only. |
| `config/atom.yml` | Curated Atom feed defaults | Default Atom source list; current checked-in file is placeholder-style sample data. |
| `config/atom.example.yml` | Example Atom feed file | Template only. |
| `config/podcasts.yml` | Podcast RSS inputs with names and per-feed limits | Default podcast feed seeds for onboarding/import flows; live subscriptions now primarily come from DB-backed configs. |
| `config/podcasts.example.yml` | Example podcast feed file | Template only. |
| `config/reddit.yml` | Default subreddit list and per-subreddit limits | Hybrid runtime input: the Reddit scraper can merge or override DB-backed sources with file-backed subreddits. |
| `config/reddit.example.yml` | Example Reddit config | Template only. |
| `config/techmeme.yml` | Techmeme feed URL plus cluster/related-link limits | Active runtime config for the scheduled Techmeme scraper. |
| `config/twitter.yml` | Twitter list IDs, cookies path, limits, lookback window, filters, and optional proxy | Available for the Twitter list scraper, but the scheduled runner currently leaves that scraper disabled. |
| `config/youtube.yml` | YouTube channel list plus yt-dlp cookies, PoToken, throttle, and player-client options | Supports YouTube ingestion and transcript-related flows even though the scheduled YouTube scraper is currently disabled. |
| `config/module_size_guardrails.json` | Per-file size limits | Checked by `scripts/check_module_size_guardrails.py` to keep large modules from growing without an explicit budget. |

## Notes
- Keep secrets and machine-specific cookie files outside this folder when possible; the checked-in config files are intended to be shareable defaults.
- When file-backed and DB-backed config coexist, prefer documenting which path is authoritative in the matching scraper/service module before changing either source.
