# app/routers/

Source folder: `app/routers`

## Purpose
Top-level FastAPI router modules for auth and the compatibility content API aggregate.

## Runtime behavior
- `auth.py` owns user authentication/profile endpoints under `/auth`: Apple sign-in, debug user creation, token refresh, `/me` reads, and `/me` profile updates.
- Auth response shaping includes X bookmark sync and council persona fields where present on the user.
- `api_content.py` aggregates content list, narration, audio episodes, stats, detail, read status, Knowledge, actions, scraper configs, submissions, and chat under `/api/content`.
- The deeper route modules live in `app/routers/api`.

## Important files
| File | Purpose |
|---|---|
| `auth.py` | User auth/profile JSON API. |
| `api_content.py` | Compatibility router that groups content-adjacent API modules under `/api/content`. |
| `__init__.py` | Package marker. |

## Integration points
- `app/main.py` mounts `auth.router` with `/auth` and `api_content.router` with `/api/content`.
- Admin HTML auth lives in `app/admin_web/auth.py`, not in this folder.
