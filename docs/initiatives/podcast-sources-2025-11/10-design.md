# Podcast Sources Feature Plan (iOS + API + Pipeline)

## Goals
- Mirror the existing feed source pattern for podcasts (per-user `UserScraperConfig` entries) with clean validation and API ergonomics for iOS.
- Move iOS Settings from inline source list to drill-down screens (feeds vs podcasts) while keeping CRUD parity.
- Ensure podcast scraper/pipeline consumes user configs consistently and keeps inbox mapping intact.

## Current State
- `UserScraperConfig` already stores all dynamic sources; allowed types: `substack`, `atom`, `podcast_rss`, `youtube` (service-level constant).
- API: `/api/scrapers` list + CRUD with generic config validation (`config.feed_url` required). No filtering by type; response forces client to dig into `config.feed_url`.
- Scraping: `PodcastUnifiedScraper` pulls active configs via `list_active_configs_by_type("podcast_rss")` → `build_feed_payloads` (limit default 10, uses `display_name`/`config.name`).
- iOS Settings (`SettingsView`) renders all configs inline in one section; `FeedDetailView` edits all types. `ScraperConfigService` has no filtering API.

## Gaps / Decisions
1) **Type-aware API contract** (for iOS drill-down): add filtering to list endpoint and expose derived fields so the client does not parse `config` dictionaries.
2) **Validation**: keep shared model but enforce podcast-friendly constraints (required `feed_url`, optional `limit` range, optional `display_name`/`is_active`).
3) **UX**: split Settings into drill-down tiles → “Feed Sources” (substack/atom/youtube) and “Podcast Sources” (podcast_rss). Each screen supports list/add/edit/delete/toggle.
4) **Pipeline**: no new schema; ensure podcast configs flow unchanged into `PodcastUnifiedScraper`, carry `user_id` through `build_feed_payloads`, and keep `ensure_inbox_status` behavior.

## API Design (FastAPI)
- **Filtering**: `GET /api/scrapers?type=podcast_rss` (single) and `?types=podcast_rss,substack` (comma-separated) to scope lists. Default = all types (backward compatible).
- **Response shape**: extend `ScraperConfigResponse` with `feed_url` (string) and `limit` (int | None) derived from `config`, keeping raw `config` for compatibility.
- **Create/Update payloads**:
  - `scraper_type`: Literal[`substack`, `atom`, `podcast_rss`, `youtube`].
  - `config.feed_url`: required, trimmed URL string; `config.limit`: optional int 1–100 (default 10).
  - `display_name`: optional ≤255; `is_active`: bool.
- **Errors**: 400 for validation/duplicate feed, 404 for missing records (same as today).
- **iOS needs**: server returns only the filtered set per screen to reduce client-side filtering.

## Backend Implementation Steps
1) Pydantic models: tighten `CreateUserScraperConfig`/`UpdateUserScraperConfig` with Literal types, limit validation, and `feed_url` normalization (strip whitespace, ensure non-empty).
2) API list: accept `type`/`types` query params → filter before returning; include `feed_url` + `limit` in response.
3) API CRUD: ensure `feed_url` stored in column and config remains normalized; keep uniqueness constraint behavior.
4) Scraper integration: reuse `build_feed_payloads` (already yields `limit`, `user_id`, `config_id`); add tests to ensure podcast configs are surfaced with provided limits.
5) Tests: expand router/service tests for podcast paths, filtering, limit validation, and duplicate handling.

## iOS UX / Client Changes
- Settings top-level: replace inline list with two navigation rows:
  - “Feed Sources” → list of `substack`/`atom`/`youtube`.
  - “Podcast Sources” → list of `podcast_rss`.
- Create two list views (can share a generic view with type parameter) that:
  - Fetch via `listConfigs(type: ...)`.
  - Show derived `displayName`/`feedURL`/active badge.
  - Support add/edit/delete/toggle using existing endpoints with `scraper_type` preset.
- Add optional `limit` field to add/edit UI (default 10) to match backend contract.
- Update models/services:
  - `ScraperConfigService.listConfigs(type:)` adding query param.
  - `ScraperConfig` to read `limit` if present (optional).
  - ViewModels to maintain separate collections for feeds vs podcasts (or one generic with filter).
- Maintain existing behavior for other settings sections.

## QA / Acceptance
- API: pytest coverage for `type`/`types` filtering, podcast create/update (valid/invalid URL, limit bounds), duplicate rejection, and response fields (`feed_url`, `limit`).
- Scraper: unit/integration to confirm `PodcastUnifiedScraper._load_podcast_feeds` returns user configs with limit and name; ensure `ensure_inbox_status` is called for podcast content.
- iOS: manual or UI tests for:
  - Navigation from Settings → Feed Sources / Podcast Sources.
  - List renders correct filtered items.
  - Add/edit/delete/toggle flows for podcast sources; validation errors surfaced.
  - Limit optional field respected (saved + reflected in list).
- Docs: add short how-to for podcast source configuration (API + iOS) in README/docs; note new query params.

## Open Questions
- Keep `youtube` in feeds list? (default yes; treat as “feed sources”.)
- Need server-side URL normalization beyond trim/lowercase? (assume trim/https normalize only; no heavy canonicalization.)
