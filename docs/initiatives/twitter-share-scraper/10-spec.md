# Twitter Share Scraper (Tweet-Only)

## Summary
Implement a tweet-only ingestion path for share-sheet submissions. When a user shares a tweet URL, fetch tweet details via X/Twitter GraphQL (cookie-auth), extract external URLs, and process as **articles**. If the tweet has external URLs, use them as the scraping targets while keeping the tweet as the discussion/source link. If no URLs exist, summarize the tweet text directly as article content. No list/search/timeline scraping.

## Goals
- Handle **only** tweet URLs submitted via share/bookmark.
- Use tweet URL as **discussion_url** in all cases.
- If tweet contains external URLs, scrape those URLs; tweet remains the source context.
- If tweet has no external URLs, summarize tweet text as the article content.
- Content should appear as **article** type with platform `twitter`.
- Use robust GraphQL TweetDetail flow inspired by `../bird` (query-id fallback + refresh, note-tweet/article extraction, cookie auth).
- Explicitly determine whether this works on remote hosts (no browser cookies) vs local (browser cookies).

## Non-Goals
- No support for timelines, lists, bookmarks, searches, or scheduled Twitter scrapes.
- No Playwright-based scraping for tweets (avoid `twitter_unified` path).
- No media download (images/videos) beyond metadata references for now.

## Current Behavior (2026-01-04)
- Share sheet submits `POST /api/content/submit` with a URL; `ANALYZE_URL` infers content type and then `PROCESS_CONTENT` runs URL strategies.
- `twitter_unified` exists for list scraping but is not wired for share-sheet tweet ingestion.
- No tweet-specific URL handling in `url_detection`.

## Proposed Design

### 1) Tweet URL Detection + Canonicalization
- Add a tweet URL detector in `app/services/url_detection.py`:
  - Match `twitter.com/<user>/status/<id>` and `x.com/<user>/status/<id>`.
  - Canonicalize to `https://x.com/i/status/<id>` for API calls.
- When detected during `ANALYZE_URL`:
  - Force `content_type=article`.
  - Set `platform=twitter` in `Content` and `content_metadata["platform"]`.
  - Skip LLM analysis (no HTML fetch).

### 2) Twitter GraphQL Client (Borrow Best Ideas from `../bird`)
Implement a minimal Python client patterned after `../bird`:
- **Auth**
  - Require `auth_token` + `ct0` cookies.
  - Build `cookie` header as `auth_token=<...>; ct0=<...>`.
  - Use static bearer token (same as bird) for GraphQL.
  - Allow env overrides: `TWITTER_AUTH_TOKEN`, `TWITTER_CT0`, optional `TWITTER_USER_AGENT`.
- **TweetDetail API**
  - Call `https://x.com/i/api/graphql/<queryId>/TweetDetail` with variables + features.
  - Use **query-id fallback list** (baked IDs) + refresh on 404.
  - Implement **runtime query-id refresh** like bird:
    - Scrape X client bundles from public pages (home/explore/notifications/settings) and cache `queryId` in a local JSON cache.
    - TTL cache (24h default), refresh on 404.
- **Tweet parsing**
  - Extract text in priority order: article text -> note tweet text -> legacy `full_text`.
  - Extract author name/username, created_at, reply/retweet/like counts.
  - Extract `entities.urls` expanded URLs; drop `t.co`, `twitter.com`, `x.com`.

### 3) Tweet Share Processing Strategy (Selected URL Handling)
Add a new `TwitterShareProcessorStrategy` in `app/processing_strategies/` that handles tweet URLs.

**Selected URL handling (Option A): Resolve tweet during ANALYZE_URL**
- In `AnalyzeUrlHandler` (`app/pipeline/handlers/analyze_url.py`), if tweet URL:
  - Fetch tweet details via Twitter client (main tweet + thread).
  - Store tweet metadata (`tweet_*`, `discussion_url`, `tweet_url`, `author`, counts, created_at) in `content_metadata`.
  - If **external URLs exist**:
    - Set `content.url` to the **first external URL**.
    - For **each additional external URL**, create a new Content row (article) with the same tweet metadata and enqueue `ANALYZE_URL`.
  - If **no external URLs**:
    - Keep `content.url` as the tweet URL.
    - Set `tweet_only=true` and use combined tweet/thread text for summarization.

### 4) Metadata Mapping
For tweet shares (articles):
- `content.content_type = article`
- `content.platform = twitter`
- `content.url = <external article url>` when present; otherwise the tweet URL.
- `content.source_url = <tweet url>` (original submission).
- `content_metadata` additions:
  - `discussion_url`: tweet URL (required)
  - `tweet_id`, `tweet_url`, `tweet_author`, `tweet_author_username`
  - `tweet_created_at`, `tweet_like_count`, `tweet_retweet_count`, `tweet_reply_count`
  - `tweet_text` (raw main tweet text)
  - `tweet_thread_text` (combined main + thread text, chronological)
  - `tweet_external_urls`: list of expanded external URLs

### 5) API Exposure
- Consider adding `discussion_url` to `ContentSummaryResponse` and `ContentDetailResponse` for **articles**, not just news.
- Alternatively rely on `metadata.discussion_url` in detail responses only (no list UI).
- Decision: prefer explicit field for consistent UI usage.

### 6) Remote Host Viability
- **Local host**: browser cookie extraction is not used; only env vars.
- **Remote host**: works if `TWITTER_AUTH_TOKEN` + `TWITTER_CT0` are provided via env/secret manager.
- **Guest access**: do not rely on guest tokens for TweetDetail; treat as unsupported/fallback with explicit log warnings.
- **Operational risk**: query-id rotation and bot protections can break GraphQL calls; mitigate via cached query-id refresh and short error logs.

### 7) Logging + Error Handling
- Use structured logging:
  - `component="twitter_share"`, `operation="fetch_tweet" | "parse_tweet" | "resolve_external_url"`.
- Do **not** log full cookies or full tweet text; redact when needed.
- If tweet fetch fails:
  - Mark content as failed with a clear error message.
  - Do not retry indefinitely (respect `max_retry_attempts`).

## Data Flow
1. Share sheet submits tweet URL.
2. `ANALYZE_URL` detects tweet, fetches tweet details.
3. If external URLs present:
   - Set `content.url` to the first external URL.
   - Create additional content items for remaining URLs (all articles).
   - Keep tweet info in metadata for each.
4. `PROCESS_CONTENT` runs standard HTML/PDF strategies to extract content.
5. `SUMMARIZE` runs as article.

If no external URL:
- Use combined tweet + thread text as `text_content` and summarize as article.

## Config / Env
- `TWITTER_AUTH_TOKEN` (required)
- `TWITTER_CT0` (required)
- `TWITTER_USER_AGENT` (optional)
- `TWITTER_QUERY_ID_CACHE` (optional path; default under app data dir)

Add to `.env.example` and `app/core/settings.py` as needed.

## Tests
- Unit: tweet URL detection + canonicalization.
- Unit: GraphQL response parsing (note tweet, article, legacy text, thread ordering).
- Unit: URL extraction and filtering (t.co/twitter/x.com removed).
- Integration: ANALYZE_URL tweet path updates content metadata and URL + fans out for multiple external URLs.
- Integration: tweet-only (no external URL) produces article summary using thread text.
- Regression: existing non-twitter URLs unchanged.

## Open Questions
None for v1.

## Rollout Notes
- No DB migrations.
- Keep `twitter_unified` disabled for scheduled scrapes.
- Add feature flag if needed to gate tweet handling in production.
