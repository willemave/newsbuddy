# app/

Source folder: `app`

## Purpose
Application root for the FastAPI process and shared backend package namespace.

## Runtime behavior
- `app/main.py` creates the FastAPI application, loads settings, initializes logging and the database, installs middleware, mounts static folders, registers routers, and exposes public policy and health endpoints.
- `app/openapi.py` provides stable OpenAPI operation IDs through `build_operation_id`.
- `app/constants.py` stores cross-package constants such as aggregator subscription markers.
- Request middleware adds request IDs, structured request/response logging, skipped log-path handling, and cache-control behavior for versioned generated image assets.
- `/` redirects to `/admin`; `/health` performs a lightweight DB readiness check.

## Mounted routes and static assets
| Mount | Owner |
|---|---|
| `/static/images` | Generated content images from `settings.images_base_dir`. |
| `/admin/static` | Jinja admin CSS/static assets. |
| `/auth` | `app/routers/auth.py` plus admin auth routes. |
| `/admin` | `app/admin_web/router.py`. |
| `/api/content` | Compatibility content router aggregation from `app/routers/api_content.py`. |
| `/api/news` | Short-form news API. |
| `/api/learning` | Authenticated Learning Deck APIs. |
| `/learning/share/*`, `/learning/signed/*` | Public/private hosted Learning Deck artifacts. |
| `/audio/share/*` | Public shared audio episode playback artifacts. |
| `/api/*` | Direct API routers for interactions, feedback, scrapers, discovery, onboarding, integrations, agent APIs, and OpenAI transcription. |

## Important files
| File | Purpose |
|---|---|
| `app/main.py` | FastAPI app construction and runtime wiring. |
| `app/openapi.py` | Operation ID generation for generated clients/contracts. |
| `app/constants.py` | Constants shared across models, services, and scrapers. |
| `app/__init__.py` | Package marker. |

## Integration points
- Static mount paths depend on `app/core/settings.py`.
- Routers depend on `app/core/deps.py` for authenticated users and DB sessions.
- Queue and worker behavior starts elsewhere (`app/pipeline`, `scripts/run_workers.py`, Docker entrypoints) but shares the same settings/model layer.
