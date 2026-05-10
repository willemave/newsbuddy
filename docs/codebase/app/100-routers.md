# app/routers/

Source folder: `app/routers`

## Purpose
Top-level FastAPI routers for client authentication and the compatibility bridge that mounts the API router under legacy imports.

## Runtime behavior
- Owns Apple sign-in, token refresh, and current-user profile endpoints.
- Keeps the root API package decoupled by exposing a thin compatibility re-export in `api_content.py`.
- Admin HTML pages, diagnostics, and admin login/logout live under `app/admin_web/`.

## Inventory scope
- Direct file inventory for `app/routers`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/routers/__init__.py` | n/a | Supporting module or configuration file. |
| `app/routers/api_content.py` | n/a | API endpoints for content with OpenAPI documentation |
| `app/routers/auth.py` | `apple_signin`, `debug_create_user`, `refresh_token`, `get_current_user_info`, `update_current_user_info` | Authentication endpoints. |
