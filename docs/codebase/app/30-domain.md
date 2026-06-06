# app/models/domain/

Source folder: `app/models/domain`

## Purpose
Internal domain transfer objects and helpers that sit between ORM rows, metadata contracts, service logic, and API response shaping.

## Runtime behavior
- Provides canonical content/domain objects without requiring services and queries to know raw ORM column details.
- Keeps explicit ORM bridges in mapper modules so most domain helpers remain independent of SQLAlchemy.
- Holds display and form helpers used by API response builders and list/detail readiness rules.

## Important files
| File | Purpose |
|---|---|
| `content.py` | Canonical content-form and type helpers. |
| `content_mapper.py` | Converts between ORM `Content` rows and canonical `ContentData`. |
| `content_display.py` | Resolves image URLs, long-form summary readiness, and feed-subscribe affordances. |
| `chat_render.py` | Chat render metadata domain helpers. |
| `discovery.py` | Discovery result/domain shapes. |
| `scraper_runs.py` | Scraper stats used by runner and logging. |
| `user_profile.py` | User profile and council persona domain helpers. |

## Integration points
- `app/routers/api/content_responses.py` uses display helpers when shaping content list/detail DTOs.
- `app/pipeline`, `app/services`, and `app/queries` use domain objects to keep business logic away from raw ORM row details.
