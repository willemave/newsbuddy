# app/models/

Source folder: `app/models`

## Purpose
Shared model layer split by boundary: database rows, public API DTOs, metadata contracts, internal payloads, domain objects, LLM schemas, and enum/value contracts.

## Boundary rules
- `app.models.contracts` stays dependency-light and is safe for cross-layer imports.
- `app.models.db` owns SQLAlchemy ORM rows and should not import API DTOs, services, or routers.
- `app.models.api` owns public request/response models and OpenAPI component names.
- `app.models.metadata` owns persisted JSON metadata contracts and tolerant accessors.
- `app.models.domain` bridges internal transfer objects and explicit mappers.
- `app.models.internal` and `app.models.llm` are private to workers/services and should not import routers.

## Inventory
| Path | Purpose |
|---|---|
| `contracts.py` | Shared enums such as content/news/task states, queues, LLM providers, summary kinds, Learning Deck source/run/status values, and visibility/status contracts. |
| `db/` | ORM tables for users, content, news, processing tasks, discovery/onboarding, scraper configs, integrations, API keys, CLI links, chat, analytics, feedback, usage, audio episodes, and learning decks. |
| `api/` | FastAPI DTOs for auth/users, content, actions, discussions, submissions, chat, news, discovery, onboarding, scraper configs, integrations, jobs, agent, analytics, feedback, OpenAI, API keys, CLI, audio episodes, learning decks, and pagination. |
| `domain/` | Internal transfer objects, content mappers/display helpers, chat render metadata, user profile/council persona helpers, discovery results, and scraper run stats. |
| `metadata/` | Base/source metadata, summaries, article/podcast/news/insight report metadata, long-form artifacts, discussion metadata, accessors, state helpers, validation, and summary-kind helpers. |
| `internal/` | Assistant context, admin eval payloads, feed backfill payloads, and scraper config command payloads. |
| `llm/` | Strict structured-output schemas for content analysis and feed discovery. |

## Runtime behavior
- Alembic/startup imports `app.models.db` for table registration.
- Services and mappers validate/normalize JSON metadata rather than relying on ORM assignment hooks.
- API modules preserve public field names consumed by iOS, CLI, agents, and admin-adjacent tools.
