# Agentic Feed + Podcast + YouTube Discovery (Weekly)

## Summary
Use favorited content as seeds to plan multi-lane Exa searches (with controlled randomness), summarize results, and surface new RSS/blog feeds, podcasts, and YouTube channels per user. Results are persisted as suggestions, exposed via API, and can be subscribed or added as single items when available. Runs weekly via cron and avoids already-subscribed sources.

## Goals
- Weekly background discovery per user based on favorites.
- Agentic lane planning + Exa search + summarization.
- Return **up to 5–10 RSS/blog feeds** and **up to 5–10 podcast feeds**, plus **YouTube channels** when relevant.
- Best-effort mix of smallweb + Substack (no forced distribution).
- Dedupe against existing subscriptions and prior suggestions (ever).
- Persist results and allow user subscription via API.

## Non-Goals
- Full recommender system or continuous real-time updates.
- Auto-subscribing users without consent.
- Expanding scraping coverage beyond RSS/podcast feeds and YouTube channels.
- Forcing a fixed distribution of source types.

## Current Behavior (2026-01-09)
- Users can favorite content; favorites are stored in `content_favorites`.
- Feed detection exists during content submission (`feed_detection`).
- Users can subscribe to feeds via `user_scraper_configs`.
- No automated discovery or weekly job exists.

## Proposed Changes

### 1) Data Model: Discovery Runs + Suggestions
Add new tables (names TBD):

**`feed_discovery_runs`**
- `id`, `user_id`, `status` (pending|completed|failed)
- `direction_summary` (text)
- `seed_content_ids` (JSON list)
- `created_at`, `completed_at`

**`feed_discovery_suggestions`**
- `id`, `run_id`, `user_id`
- `suggestion_type` (`atom|substack|podcast_rss|youtube`)
- `site_url`, `feed_url`, `item_url`, `title`, `description`
- `channel_id`, `playlist_id` (YouTube only, optional)
- `rationale` (why it matches favorites)
- `score` (float)
- `status` (`new|dismissed|subscribed`)
- `created_at`, `updated_at`
- `config` (JSON; normalized config payload used to create `user_scraper_configs`)

Notes:
- Keep `feed_url` required for subscription; `site_url` optional.
- For YouTube suggestions, set `feed_url` to the channel/playlist URL and store `channel_id`/`playlist_id`.
- `item_url` stores a specific episode/video URL when available (used to add a single item without subscribing).
- Include `metadata` JSON if we need model trace or source hints.

### 2) Favorite Corpus + Direction Selection
Build a compact context from favorites:
- Prefer **completed** items with summaries/metadata.
- Include **all content types** (article/podcast/news/etc.).
- No minimum favorites required; best-effort with whatever favorites exist.
- Cap to ~20 favorites per run.
- Stratify by **recency** + **source diversity**.

LLM step: **Direction Selector** (based on all favorites)
- Output 2–4 exploration directions (e.g., “AI infrastructure ops blogs”, “macro + venture podcasts”).
- For each direction: list the subset of favorite IDs that justify it + a short rationale.
- This enables *different directions per run* without using all favorites every time.

Randomness injection: removed. Direction selection and lane planning are deterministic based on favorites.

### 3) Lane Planning + Query Sets (ReviewBuddy-inspired)
Reuse the ReviewBuddy lane pattern:
- `DiscoveryLane { name, goal, seed_queries[] }`
- Separate queries for **blogs/RSS**, **podcasts**, and **YouTube channels**.
- 2–4 queries per lane; 3–6 lanes per run.

Heuristics inside query generation:
- Prefer “site:substack.com” queries in 1–2 lanes.
- Prefer “RSS feed”, “Atom feed”, “podcast RSS”, “listen” style queries.
- Include *smallweb* signals (personal blog, newsletter, indie podcast, etc.).
- Include YouTube patterns (e.g., “site:youtube.com channel”, “YouTube channel about <topic>”).

### 4) Agentic Search + Summarization
For each lane (sequentially, no parallel requirement):
- Run Exa search for each query (using `app/services/exa_client.exa_search`).
- Agent summarizes results and proposes **candidate sources** (site URLs and/or feed URLs).
- Collect candidates across lanes, then normalize + dedupe.

Suggested candidate schema:
```python
class DiscoveryCandidate(BaseModel):
    title: str | None
    site_url: str
    feed_url: str | None
    item_url: str | None
    suggestion_type: Literal["atom", "substack", "podcast_rss", "youtube"] | None
    channel_id: str | None
    playlist_id: str | None
    rationale: str
    evidence_urls: list[str]
    config: dict[str, Any] | None
```

### 5) Feed + Podcast Validation
Use existing `FeedDetector` to validate candidate sources:
- If candidate has `feed_url`, validate + classify.
- If only `site_url`, attempt feed discovery (HTML → candidates → Exa fallback).

Podcast handling:
- Prefer **RSS feeds** from known podcast hosts or detected in page HTML.
- If only a podcast landing page is found, attempt to resolve RSS via feed detection.

YouTube handling:
- Normalize channel URLs and resolve `channel_id` (or `playlist_id`) when possible.
- Build a YouTube scraper config from resolved identifiers.
- Store `suggestion_type="youtube"` with `config` that includes `channel_id` or `playlist_id`
  plus a `feed_url` set to the channel or playlist URL for compatibility with existing validators.
- If only a YouTube watch URL is found, store it as `item_url` and keep `feed_url` only when a
  channel/playlist can be resolved. Users can still add the single item.

Podcast handling (item-level):
- If a specific episode URL is found, store it as `item_url` so users can add just that episode.

### 6) Selection, Quotas, and “Smallweb” Mix
Scoring factors (tunable):
- Relevance to direction + favorites (LLM score or heuristic)
- Novelty (not already subscribed; not already suggested ever)
- Source diversity (unique domains)

Quotas (best-effort, no minimums):
- Up to 5–10 RSS/blog feeds
- Up to 5–10 podcast feeds
- YouTube channels as best-effort additions (not counted toward RSS/podcast quotas)
- No forced distribution; best-effort mix

Smallweb heuristic (v1):
- Domain NOT in `BIG_PLATFORM_DOMAINS` (medium.com, substack.com, etc.)
- Title/site indicates personal/indie
- Avoid giant media networks unless explicitly relevant

### 7) Persistence + API Surface
**Store** suggestions tied to a run and expose via API.

Proposed endpoints:
- `GET /api/discovery/suggestions` → latest run (grouped by type)
- `POST /api/discovery/refresh` → enqueue a new run (manual refresh)
- `POST /api/discovery/subscribe` → subscribe selected suggestion IDs
- `POST /api/discovery/add-item` → add single item(s) from suggestions (uses `item_url`)
- `POST /api/discovery/dismiss` → mark suggestions dismissed
- `POST /api/discovery/clear` → dismiss all suggestions for the user

Subscription action:
- Map suggestion to `user_scraper_configs` using existing creation flow.
- After subscribe, mark suggestion `status=subscribed`.
- Suggestions do **not** expire automatically; users can clear them.

### 7.1) UI (iOS)
- Add a tabbed experience under the Knowledge tab:
  - **Discover** (grouped lists: Feeds / Podcasts / YouTube)
  - **Existing** (current subscriptions; optional)
- Include a **Manual Refresh** action to enqueue a new run (no loading state required).
- Each suggestion should offer:
  - **Subscribe** (feed/podcast/youtube channel) when possible
  - **Add item** when `item_url` is available (single episode/video)

### 8) Weekly Scheduling (Cron)
Add a weekly job:
- Script: `scripts/run_feed_discovery.py`
- For each user with **≥1 favorite**, enqueue a new task `DISCOVER_FEEDS`.
- `SequentialTaskProcessor` handles the new task type by calling the discovery workflow.

Suggested crontab:
```
0 3 * * 1 cd /app && /app/.venv/bin/python scripts/run_feed_discovery.py >> /var/log/cron.log 2>&1
```

### 9) Logging + Safety
- Use structured logging with `component="feed_discovery"` and `operation=...`.
- If LLM or Exa fails, mark run failed and continue; do not affect other jobs.
- Avoid logging full favorites text; log IDs + counts only.

## Tests
- Unit: favorite sampling + direction selection input shaping.
- Unit: candidate dedupe + smallweb quota selection.
- Unit: feed validation with known RSS/podcast samples.
- Integration: run discovery end-to-end with mocked Exa and LLM.
- API: list suggestions + subscribe + dismiss.
