# app/models/

Source folder: `app/models`

## Purpose
Shared model layer split by boundary:

- `contracts.py`: canonical enums and tiny shared value contracts.
- `db/`: SQLAlchemy ORM rows only.
- `api/`: public FastAPI request/response DTOs only.
- `domain/`: internal app transfer objects, display helpers, and explicit mappers.
- `metadata/`: persisted `content_metadata` and `raw_metadata` contracts plus tolerant accessors.
- `internal/`: private worker/command/service payloads.
- `llm/`: strict LLM input/output schemas.

## Boundary Rules
- `app.models.contracts` imports no `app.*` modules.
- `app.models.db` may import `app.core.db`, `app.models.contracts`, `app.models.db.common`, and lightweight utility helpers.
- `app.models.db` must not import `app.models.api`, `app.models.domain`, `app.models.metadata`, `app.services`, or `app.routers`.
- `app.models.api` may import contracts, API DTOs, small domain value objects when necessary, and internal request context objects when they are part of the HTTP payload.
- `app.models.api` must not import `app.models.db`, `app.services`, or `app.routers`.
- `app.models.domain` may import contracts, metadata contracts, and DB models only from explicit mapper modules.
- `app.models.metadata` may import contracts and other metadata modules.
- `app.models.metadata` must not import DB, API, services, or routers.
- `app.models.internal` and `app.models.llm` must not import routers.

## Runtime Behavior
- `app.models.db` is imported by Alembic/startup for SQLAlchemy model registration.
- Metadata validation and normalization happens in services, mappers, and metadata helpers, not ORM assignment hooks.
- API modules preserve public response/request field names and OpenAPI component names.
- Domain mappers are the only model modules that intentionally bridge ORM rows and app transfer objects.

## Inventory
| Path | Purpose |
|---|---|
| `app/models/contracts.py` | `ContentType`, `ContentStatus`, `ContentClassification`, `TaskType`, `TaskQueue`, `TaskStatus`, `SummaryKind`, `SummaryVersion`, `NewsItemVisibilityScope`, `NewsItemStatus`, `MessageProcessingStatus`, `LLMProvider` |
| `app/models/db/` | ORM tables for content, news, tasks, users, discovery, onboarding, scraper configs, integrations, API keys, CLI links, chat, analytics, feedback, usage |
| `app/models/api/` | FastAPI DTOs for content, actions, discussions, submissions, chat, users, auth, news, discovery, onboarding, scraper configs, integrations, jobs, agent, analytics, feedback, OpenAI, API keys, CLI, pagination |
| `app/models/domain/` | `ContentData`, content mappers, display/form helpers, chat render metadata, user profile/council persona helpers, scraper run stats, discovery results |
| `app/models/metadata/` | Base metadata, summaries, article/podcast/news/insight report metadata, long-form artifacts, discussion placeholder, accessors, state helpers, validation, summary-kind helpers |
| `app/models/internal/` | Assistant context, admin eval payloads, feed backfill payloads, scraper config command payloads |
| `app/models/llm/` | Feed discovery planning/output schemas and content analysis structured-output schemas |
