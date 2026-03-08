# app/presenters/

Source folder: `app/presenters`

## Purpose
Presentation shaping layer that turns domain content into list/detail API responses with image URLs, readiness checks, and feed-subscription affordances.

## Runtime behavior
- Decides when content is ready to appear in list endpoints and how summary fields should be projected into response DTOs.
- Resolves public image/thumbnail URLs and attaches derived metadata that clients need without exposing raw storage details.

## Inventory scope
- Direct file inventory for `app/presenters`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/presenters/content_presenter.py` | `resolve_image_urls`, `is_ready_for_list`, `build_content_summary_response`, `build_content_detail_response`, `build_domain_content`, `can_subscribe_for_feed` | Presenters for content API responses. |
