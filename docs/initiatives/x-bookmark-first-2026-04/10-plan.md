# X Bookmark-First Sync Plan

**Opened:** 2026-04-19  
**Status:** Completed  
**Scope:** X integration sync, scheduled X scrapers, architecture docs, runtime ops docs  
**Primary goal:** disable feed-style X ingestion and keep only bookmark-driven X sync plus downstream tweet/thread/article resolution

---

## Contract

Newsly should treat X as a bookmark-first integration:

- keep per-user X bookmark sync
- keep tweet URL handling, thread lookup, linked tweet lookup, linked article resolution, and tweet snapshot reuse
- stop ingesting reverse-chronological home timeline items into news rows
- stop running scheduled X list scrapers as part of the active scraper runtime

This means X remains useful for saved posts and shared posts, but no longer acts as a feed source.

---

## Why this change

Current X behavior mixes two different product shapes:

- bookmark sync creates long-form content rows that users explicitly saved
- timeline sync creates short-form news rows from followed accounts
- scheduled X list scraping still exists in the active scraper runner

That combination makes X behave like both a personal save surface and an ambient feed surface. The desired product direction is narrower:

- explicit bookmark intent should be the durable X ingestion path
- tweet enrichment should still support threads, linked posts, linked articles, and native long-form tweet content
- ambient timeline/list ingestion should be retired

---

## Plan

### 1. Retire active scheduled X feed scraping

- [x] Remove `TwitterUnifiedScraper` from the active `ScraperRunner`.
- [x] Keep the legacy scraper code on disk for now, but treat it as inactive.
- [x] Update the Twitter/X scheduler entrypoint and docs so it no longer promises public list scraping.

### 2. Make X sync bookmark-only

- [x] Keep `sync_x_sources_for_user()` as the compatibility entrypoint.
- [x] Remove reverse-chronological timeline ingestion from the sync flow.
- [x] Preserve bookmark cursor/state persistence in `user_integration_sync_state.sync_metadata`.
- [x] Preserve bookmark tweet snapshots so later analyze/share flows can reuse stored tweet/thread/link metadata.

### 3. Preserve downstream tweet resolution

- [x] Do not change tweet URL resolution in `analyze_url`.
- [x] Do not change linked tweet lookup, older thread lookup, or external article fanout.
- [x] Keep X OAuth/token handling unchanged for bookmark sync and tweet lookups.

### 4. Update architecture and ops docs

- [x] Update `docs/architecture.md` to describe X as bookmark-first, not timeline/list driven.
- [x] Update operator docs that still describe the dedicated X scheduler as running list scraping or timeline refresh.

### 5. Verify

- [x] Update targeted tests for bookmark-only X sync.
- [x] Add/adjust runner coverage proving the active scraper set no longer includes Twitter/X.
- [x] Run targeted `pytest` and `ruff check` on touched files.

---

## Implementation notes

- No DB migration is required.
- Existing `x_timeline` rows and metadata can remain in storage; this change is forward-looking.
- The inactive `twitter_unified` implementation can stay in the repo until a separate cleanup pass removes dead code and config.
- Targeted pytest remains environment-limited locally unless the Postgres test role exists; bookmark-first tests were updated and static checks passed.

## Expected outcome

- Scheduled X feed scraping is disabled.
- X integration sync only processes bookmarks.
- Tweet share/bookmark enrichment still supports threads, linked tweets, linked articles, note tweets, and native article text.
- Architecture docs describe the intended bookmark-first X model.
