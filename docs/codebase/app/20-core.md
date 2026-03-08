# app/core/

Source folder: `app/core`

## Purpose
Core runtime infrastructure: environment settings, database/session lifecycle, security primitives, FastAPI dependencies, and shared logging/timing helpers.

## Runtime behavior
- Centralizes environment-backed settings in one Pydantic settings model consumed by routers, workers, and services.
- Owns engine/session creation and the dependency functions that inject read-write or read-only SQLAlchemy sessions into FastAPI endpoints.
- Implements JWT issuance/verification plus the auth/admin dependency helpers used across the API and admin views.

## Inventory scope
- Direct file inventory for `app/core`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `app/core/__init__.py` | n/a | Core application modules. |
| `app/core/db.py` | `init_db`, `get_engine`, `get_session_factory`, `get_db`, `get_db_session`, `get_readonly_db_session`, `run_migrations` | Functions: `init_db`, `get_engine`, `get_session_factory`, `get_db`, `get_db_session`, `get_readonly_db_session`, `run_migrations` |
| `app/core/deps.py` | `AdminAuthRequired`, `get_current_user`, `get_optional_user`, `get_or_create_admin_user`, `require_admin` | FastAPI dependencies for authentication and authorization. |
| `app/core/logging.py` | `setup_logging`, `get_logger` | Functions: `setup_logging`, `get_logger` |
| `app/core/security.py` | `create_token`, `create_access_token`, `create_refresh_token`, `verify_token`, `verify_apple_token`, `verify_admin_password` | Security utilities for authentication. |
| `app/core/settings.py` | `Settings`, `get_settings` | Types: `Settings`. Functions: `get_settings` |
| `app/core/timing.py` | `timed` | Timing utilities for profiling database and service calls. |
