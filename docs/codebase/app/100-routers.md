# app/routers/

Source folder: `app/routers`

## Purpose
Top-level FastAPI routers for authentication, admin pages, admin diagnostics, and the compatibility bridge that mounts the API router under legacy imports.

## Runtime behavior
- Owns Apple sign-in, token refresh, admin login/logout, current-user profile endpoints, and admin HTML pages.
- Serves Jinja-based dashboards for operations, admin evaluation, onboarding previews, and log/error inspection.
- Keeps the root API package decoupled by exposing a thin compatibility re-export in `api_content.py`.

## Inventory scope
- Direct file inventory for `app/routers`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/routers/__init__.py` | n/a | Supporting module or configuration file. |
| `app/routers/admin.py` | `admin_dashboard`, `onboarding_lane_preview_page`, `onboarding_lane_preview`, `admin_eval_summaries_page`, `admin_eval_summaries_run` | Admin router for administrative functionality. |
| `app/routers/api_content.py` | n/a | API endpoints for content with OpenAPI documentation |
| `app/routers/auth.py` | `apple_signin`, `debug_create_user`, `refresh_token`, `get_current_user_info`, `update_current_user_info`, `admin_login_page`, `admin_login`, `admin_logout` | Authentication endpoints. |
| `app/routers/logs.py` | `list_logs`, `view_log`, `download_log`, `errors_dashboard`, `reset_error_logs` | Functions: `list_logs`, `view_log`, `download_log`, `errors_dashboard`, `reset_error_logs` |
