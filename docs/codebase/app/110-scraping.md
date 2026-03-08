# app/scraping/

Source folder: `app/scraping`

## Purpose
Scheduled feed and site scrapers plus the orchestration runner that inserts new content rows and enqueues downstream processing.

## Runtime behavior
- Implements scraper classes for Hacker News, Reddit, Substack, Techmeme, podcasts, Atom, Twitter, and YouTube.
- Normalizes source metadata, deduplicates content creation, and records scraper/event telemetry as new content is inserted.
- Bridges file-backed configs and DB-backed user scraper configs into runnable scraper payloads.

## Inventory scope
- Direct file inventory for `app/scraping`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/scraping/__init__.py` | n/a | Supporting module or configuration file. |
| `app/scraping/atom_unified.py` | `AtomScraper`, `load_atom_feeds`, `run_atom_scraper` | Unified Atom feed scraper following the new architecture. |
| `app/scraping/base.py` | `BaseScraper` | Types: `BaseScraper` |
| `app/scraping/hackernews_unified.py` | `HackerNewsUnifiedScraper` | Types: `HackerNewsUnifiedScraper` |
| `app/scraping/podcast_unified.py` | `PodcastUnifiedScraper` | Types: `PodcastUnifiedScraper` |
| `app/scraping/reddit_unified.py` | `RedditUnifiedScraper` | Types: `RedditUnifiedScraper` |
| `app/scraping/rss_helpers.py` | `resolve_feed_source` | Helpers for shared RSS/Atom feed handling. |
| `app/scraping/runner.py` | `ScraperRunner` | Types: `ScraperRunner` |
| `app/scraping/substack_unified.py` | `SubstackScraper`, `load_substack_feeds`, `run_substack_scraper` | Unified Substack scraper following the new architecture. |
| `app/scraping/techmeme_unified.py` | `TechmemeFeedSettings`, `TechmemeSettings`, `TechmemeScraper`, `load_techmeme_config` | Dedicated scraper for Techmeme clusters. |
| `app/scraping/twitter_unified.py` | `TwitterUnifiedScraper` | Types: `TwitterUnifiedScraper` |
| `app/scraping/youtube_unified.py` | `YouTubeChannelConfig`, `YouTubeClientConfig`, `YouTubeUnifiedScraper`, `load_youtube_client_config`, `load_youtube_channels` | Unified YouTube channel scraper aligned with podcast ingestion flow. |
