# app/utils/

Source folder: `app/utils`

## Purpose
Cross-cutting utility functions for URLs, pagination, dates, filesystem paths, error logging, summary normalization, and image path/URL handling.

## Runtime behavior
- Keeps low-level helpers out of routers and services while preserving shared conventions around paths, dates, pagination cursors, and summary metadata.
- Contains reusable error logging and JSON repair utilities used by multiple service modules.

## Inventory scope
- Direct file inventory for `app/utils`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/utils/__init__.py` | n/a | Utility modules for the news app. |
| `app/utils/dates.py` | `parse_date_with_tz` | Date parsing utilities with timezone normalization. |
| `app/utils/deprecation.py` | `clear_deprecated_field_cache`, `log_deprecated_field` | Helpers for logging deprecated field usage. |
| `app/utils/error_logger.py` | `log_scraper_event`, `increment_scraper_metric`, `get_scraper_metrics`, `reset_scraper_metrics` | Error Logger - Scraper metrics and event logging utilities |
| `app/utils/image_paths.py` | `get_images_base_dir`, `get_content_images_dir`, `get_news_thumbnails_dir`, `get_thumbnails_dir` | Helpers for image storage paths. |
| `app/utils/image_urls.py` | `build_content_image_url`, `build_news_thumbnail_url`, `build_thumbnail_url` | Helpers for deterministic image URLs. |
| `app/utils/json_repair.py` | `strip_json_wrappers`, `try_repair_truncated_json` | Utility helpers for cleaning and repairing JSON payloads from LLM responses. |
| `app/utils/pagination.py` | `PaginationCursor` | Pagination utilities for cursor-based pagination with opaque tokens. |
| `app/utils/paths.py` | `resolve_config_directory`, `resolve_config_path` | Utility helpers for resolving repo-relative configuration paths. |
| `app/utils/summary_metadata.py` | `infer_summary_kind_version` | Utilities for inferring summary metadata. |
| `app/utils/summary_utils.py` | `extract_short_summary`, `extract_summary_text` | Helpers for extracting summary text from metadata payloads. |
| `app/utils/url_utils.py` | `is_http_url`, `normalize_http_url` | URL normalization helpers. |
