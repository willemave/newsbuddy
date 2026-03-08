# app/repositories/

Source folder: `app/repositories`

## Purpose
Query composition helpers for content feeds and visibility rules used by list, search, stats, and recently-read endpoints.

## Runtime behavior
- Builds shared feed queries so filters for visibility, read state, and pagination stay consistent across API endpoints.
- Concentrates SQL-specific search and full-text query behavior away from routers and presenters.

## Inventory scope
- Direct file inventory for `app/repositories`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/repositories/content_feed_query.py` | `FeedQueryRows`, `apply_created_at_cursor`, `build_user_feed_query` | Shared query builders for user-visible content feed endpoints. |
| `app/repositories/content_repository.py` | `VisibilityContext`, `build_visibility_context`, `apply_visibility_filters`, `apply_read_filter`, `get_visible_content_query`, `build_fts_match_query`, `sqlite_fts_available`, `apply_sqlite_fts_filter` | Repository helpers for content visibility and flags. |
