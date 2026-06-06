# Content response builders

Source files: `app/routers/api/content_responses.py`, `app/models/domain/content_display.py`, plus focused query adapters.

## Purpose
Shape ORM/domain rows into stable API responses for content list/detail surfaces and related router payloads.

## Runtime behavior
- `content_responses.py` builds content summary and detail DTOs for `/api/content`.
- Summary responses add resolved image URLs, news fields, saved-source data, top-comment suppression, and long-form artifact preview fields.
- Detail responses add sanitized metadata, body flags, detected feed information, image URLs, discussion affordances, and `can_subscribe` state.
- `content_display.py` owns display helper rules such as `resolve_image_urls`, `is_ready_for_long_form_summary`, and `can_subscribe_for_feed`.
- Query adapters shape news-item content projections, discussion payloads, and submission statuses where they do not belong in routers.

## Important files
| File | Purpose |
|---|---|
| `app/routers/api/content_responses.py` | Content list/detail DTO builders. |
| `app/models/domain/content_display.py` | Image URL, list readiness, and feed-subscription display helpers. |
| `app/queries/news_item_content_adapter.py` | Short-form news item to content-like projection adapter. |
| `app/queries/get_content_discussion.py` | Content discussion response shaping. |
| `app/queries/list_submission_statuses.py` | Submission/feed-subscription status projection. |

## Integration points
- Routers should use these builders/adapters rather than duplicating response-shaping logic.
- Model contracts live under `app/models/api` and metadata helpers live under `app/models/metadata`.
