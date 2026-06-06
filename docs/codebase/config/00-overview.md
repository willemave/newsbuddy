# config/

Source folder: `config`

## Purpose
File-backed feed, scraper, aggregator, YouTube, and tooling configuration used by scraper bootstrapping, onboarding defaults, import scripts, and guardrail tooling.

## Runtime behavior
- `app/utils/paths.py` resolves this folder by default and supports `NEWSAPP_CONFIG_DIR` plus per-file env overrides in callers.
- The default scraper runner loads `config/aggregators.yml` plus DB/file-backed feed scrapers for Reddit, Substack, Podcast RSS, Atom, and discussion comments.
- Per-user live subscriptions primarily live in `user_scraper_configs`; file-backed config is seed/default/operator input unless a specific scraper loader says otherwise.
- Example files document expected shape without forcing local secrets or machine-specific paths into source.

## Files
| File | What it controls | Current role |
|---|---|---|
| `config/aggregators.yml` | Hacker News, Techmeme, Mediagazer, Memeorandum, SciURLs, FinURLs, Brutalist Report | Active scheduled aggregator config loaded by `app/scraping/aggregators`. |
| `config/substack.yml` | Curated Substack feeds | Default/import/onboarding seed data; live user subscriptions are DB-backed. |
| `config/substack.example.yml` | Example Substack feed file | Template only. |
| `config/atom.yml` | Curated Atom/RSS feed defaults | Default/import/onboarding seed data. |
| `config/atom.example.yml` | Example Atom feed file | Template only. |
| `config/podcasts.yml` | Podcast RSS feed inputs | Import/default feed data used by scripts and podcast scraper inputs, not the current onboarding curated-default path. |
| `config/podcasts.example.yml` | Example podcast feed file | Template only. |
| `config/reddit.yml` | Default subreddit list and limits | Onboarding/default-source input; runtime user subscriptions are DB-backed. |
| `config/reddit.example.yml` | Example Reddit config | Template only. |
| `config/techmeme.yml` | Legacy Techmeme-specific config | Backward-compatible loader support; scheduled Techmeme now comes from `aggregators.yml`. |
| `config/twitter.yml` | Legacy Twitter list scraper config | Legacy/orphaned unless the retired list-scraper flow is reintroduced. |
| `config/youtube.yml` | yt-dlp cookies, PoToken, throttle, and player-client options | Runtime config for YouTube processing and audio/video download paths; not a default scheduled scraper. |
| `config/module_size_guardrails.json` | Per-file size budgets | Consumed by `scripts/check_module_size_guardrails.py`. |

## Runtime dependencies
- Scraping/config readers use packages such as `pyyaml`, `feedparser`, `praw`, `playwright`, `yt-dlp`, and YouTube PoToken helpers depending on the source.

## Notes
- Keep secrets and machine-specific cookie files outside this folder when possible.
- When file-backed and DB-backed config coexist, document which path is authoritative in the matching scraper/service module before changing behavior.
