# app/scraping/

Source folder: `app/scraping`

## Purpose
Scheduled feed/site scrapers plus base persistence/orchestration helpers that create content/news rows and enqueue downstream processing.

## Runtime behavior
- `ScraperRunner` loads YAML-backed news aggregators from `config/aggregators.yml`, then adds Reddit, Substack, Podcast RSS, Atom, and discussion-comment scrapers.
- `BaseScraper` normalizes source metadata, saves `news_items` or long-form `contents`, dedupes inserts, logs scraper stats, and enqueues follow-up tasks such as `FETCH_NEWS_ITEM_DISCUSSION`, `ENRICH_NEWS_ITEM_ARTICLE`, `PROCESS_CONTENT`, and `FETCH_DISCUSSION`.
- Scheduled scraper scripts check queue backpressure before and between scraper runs.
- Twitter/X list scraping is retired from the scheduled runner; X sync now lives in integration services/tasks.
- YouTube config remains shared runtime configuration for YouTube processing and audio download paths, not a default scheduled scraper.

## Important files and folders
| Path | Purpose |
|---|---|
| `runner.py` | Builds and runs the default scraper list. |
| `base.py` | Shared scraper persistence, stats, dedupe, and enqueue behavior. |
| `aggregators/` | YAML-backed news aggregator registry and scraper subclasses. |
| `discussion_comments.py` | Discussion comment scraper. |
| `reddit_unified.py` | Reddit feed scraper. |
| `substack_unified.py` | Substack scraper. |
| `podcast_unified.py` | Podcast RSS scraper. |
| `atom_unified.py` | Atom/RSS feed scraper. |
| `rss_helpers.py` | Shared feed-source resolution helpers. |
| `hackernews_unified.py`, `techmeme_unified.py` | Backward-compatible shims to aggregator implementations. |
| `youtube_config.py` | yt-dlp/cookie/PoToken/player-client config loader. |

## Integration points
- File-backed config lives in `config/`; per-user subscriptions live in `user_scraper_configs`.
- Queue routing is defined by `app/pipeline/task_specs.py`.
- Scraper tests live under `tests/scraping`.
