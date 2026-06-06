# app/utils/

Source folder: `app/utils`

## Purpose
Small cross-cutting helpers for URLs, paths, dates, pagination, title normalization, summary metadata, image paths/URLs, JSON repair, error logging, and summarization inputs.

## Runtime behavior
- Utilities stay stateless and dependency-light so they can be reused by routers, services, scrapers, scripts, and tests.
- Config path helpers centralize repo-relative and env-overridden config lookup.
- Summary/title helpers normalize display and LLM-input data without importing service layers.

## Important files
| File | Purpose |
|---|---|
| `dates.py` | Date/time normalization helpers. |
| `error_logger.py` | Error log helpers. |
| `image_paths.py`, `image_urls.py` | Local image path and URL resolution. |
| `json_repair.py` | Tolerant JSON cleanup. |
| `news_titles.py`, `title_utils.py` | News/content title cleanup. |
| `pagination.py` | Cursor/pagination helpers. |
| `paths.py` | Repo/config path resolution with env overrides. |
| `summarization_inputs.py` | News/aggregator context strings for summarization. |
| `summary_metadata.py`, `summary_utils.py` | Summary metadata and text helpers. |
| `url_utils.py` | URL parsing/normalization helpers. |
| `__init__.py` | Package marker. |

## Integration points
- `config/` docs should stay aligned with `paths.py` when config env vars change.
- Keep heavy provider code in services or strategies, not in utilities.
