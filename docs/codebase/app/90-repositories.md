# app/repositories/

Source folder: `app/repositories`

## Purpose
SQLAlchemy query composition and persistence helpers. Repositories keep DB details out of routers, commands, queries, and services.

## Runtime behavior
- Content repositories build list/detail/card projections and shared feed visibility rules.
- Knowledge and read-status repositories mutate user-specific saved/read state.
- Search repository owns PostgreSQL full-text/trigram-backed search helpers.
- Stats and API-key repositories provide small persistence/query seams for API surfaces.
- User-integration repository stores encrypted provider credentials and integration state.

## Important files
| File | Purpose |
|---|---|
| `api_key_repository.py` | Machine API-key CRUD and lookup helpers. |
| `content_card_repository.py` | Card/list/recently-read projections. |
| `content_detail_repository.py` | Detail projection helpers. |
| `content_feed_query.py` | Shared list visibility, sort timestamp, and cursor helpers. |
| `content_repository.py` | Content visibility context/filter helpers. |
| `knowledge_repository.py` | Per-user Knowledge saves. |
| `read_status_repository.py` | Per-user read/unread state. |
| `search_repository.py` | Content/news search helpers. |
| `stats_repository.py` | Unread/processing/long-form metrics. |
| `user_integration_repository.py` | User-managed provider credentials and integration rows. |

## Integration points
- Commands and queries should call repositories/services instead of building route-local SQL.
- Raw SQL should stay parameterized; prefer SQLAlchemy expressions for shared query fragments.
