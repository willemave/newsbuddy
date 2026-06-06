# app/core/

Source folder: `app/core`

## Purpose
Infrastructure authority for settings, database lifecycle, authentication/security primitives, FastAPI dependencies, structured logging, observability metadata, redaction, model defaults, and lightweight timing helpers.

## Runtime behavior
- `settings.py` loads environment-backed Pydantic v2 settings and nested queue/storage/provider configuration.
- `db.py` owns SQLAlchemy engine/session setup, dependency factories, and engine disposal after transient DB failures.
- `deps.py` provides request dependencies such as current-user loading, admin checks, readonly sessions, and `require_user_id`.
- `security.py` handles JWTs, Apple Sign In token verification helpers, password hashing, admin session tokens, and API auth primitives.
- `logging.py`, `observability.py`, and `redaction.py` normalize structured log payloads and keep sensitive fields out of logs.
- `model_defaults.py` centralizes default LLM/provider model choices.

## Important files
| File | Purpose |
|---|---|
| `app/core/settings.py` | Pydantic settings, env aliases, production validation, storage/provider/queue configuration. |
| `app/core/db.py` | Engine construction, session factories, readonly DB dependencies, startup initialization, engine disposal. |
| `app/core/deps.py` | FastAPI dependencies for users, admin access, API keys, and DB sessions. |
| `app/core/security.py` | JWT, Apple token, password, admin session, and token helpers. |
| `app/core/api_keys.py` | API key generation, formatting, hashing, and verification helpers. |
| `app/core/logging.py` | Logger setup and structured logger access. |
| `app/core/observability.py` | Common log `extra` builders and task/request event naming. |
| `app/core/redaction.py` | Recursive sensitive-field redaction for logs and diagnostics. |
| `app/core/model_defaults.py` | Default model/provider constants. |
| `app/core/timing.py` | Small timing utilities. |

## Integration points
- `app/main.py`, routers, services, admin pages, workers, scripts, and migrations all load settings or DB sessions from this package.
- Production behavior should keep secrets in settings/env and never hardcode them in callers.
