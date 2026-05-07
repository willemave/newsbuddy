# Newsly Agent Guide

Start with this file. Open [docs/architecture.md](docs/architecture.md) only when a task crosses package boundaries, changes data/API contracts, touches workers/queues, or needs system-level context.

Use [docs/codebase/](docs/codebase/) only for the specific folder or surface you are modifying.

This file stays minimal and only captures repo-specific working rules.

## Core Rules

- Keep replies short, technical, and complete.
- Never commit or push unless explicitly asked.
- If asked to commit, commit to the current checked-out branch unless explicitly asked to create or use a different branch. This applies even if the current branch is `main`.
- Prefer small, local changes that follow the existing layer boundaries.
- Keep durable architecture notes in `docs/architecture.md`; keep this file limited to agent operating rules and routing.

## Working Shape

- Backend: FastAPI, SQLAlchemy 2, Pydantic v2, database-backed async task queue.
- Clients: SwiftUI iOS app, iOS Share Extension, Jinja admin UI, machine-facing APIs.
- UI note: this repo is not a React app. Web UI is Jinja-rendered; mobile UI is SwiftUI.
- Runtime note: local development should use the normal local services and a local PostgreSQL instance. Treat Docker as a staging/production runtime, not the default local-dev path.
- Operator note: use the `admin` CLI for Docker-runtime inspection and repairs. `admin logs tail` defaults to the unified `newsly` container log stream.

## Context Routing

| Task | Read |
| --- | --- |
| Backend route/API change | `docs/codebase/app/100-routers.md`, relevant router and API model files |
| Queue or ingestion change | `docs/architecture.md` workers/tasks sections, relevant handler or strategy |
| iOS UI/API change | Matching files under `docs/codebase/client/` plus the touched Swift files |
| Admin CLI or production debug | `admin/`, `tests/admin/`, and the operator notes in this file |
| Unknown ownership | `docs/architecture.md` package ownership section only, then narrow to touched files |

## Dependency Direction

For backend changes, follow this order:

1. routers
2. commands/queries
3. repositories/services
4. models/infrastructure

For processing changes, follow this order:

1. task type or handler
2. worker/service orchestration
3. strategy or provider implementation
4. persistence and response updates

## Workflows

Backend change:

1. Find the owning router and request/response models.
2. Put orchestration in commands/queries, not routers.
3. Keep DB access in repositories/services.
4. Add or update focused tests under `tests/`.
5. Run `ruff check` on touched Python files and relevant `pytest`.

Processing change:

1. Identify the task type, handler, and queue ownership.
2. Trace persistence and retry semantics before changing provider code.
3. Keep strategy/provider code focused on extraction, transformation, or external calls.
4. Add tests for success, malformed input, and retry/failure behavior when production behavior changes.

Production debug:

1. Confirm whether the user is testing production or local.
2. Use `uv run -m admin logs exceptions --limit 20` for recent failures.
3. Use `uv run -m admin logs tail --limit 200` only after narrowing the symptom.
4. Prefer DB, log, runtime, and queue evidence over local speculation.
5. Do not patch production directly unless explicitly asked.

## Code Rules

- Prefer functions over classes unless stateful objects clearly improve the design.
- Use full type hints and validate boundary inputs with Pydantic v2.
- Favor guard clauses and straightforward control flow over nested branches.
- Use `lower_snake_case` for Python names and UPPER_CASE for constants.
- Do not hardcode secrets; keep config in `app/core/settings.py`.
- Use parameterized DB access, never SQL built with f-strings.
- Log errors with `logger.error()` or `logger.exception()` and structured `extra` fields.

## Local Patterns

FastAPI route shape:

```python
@router.get("/items", response_model=ContentListResponse)
def list_news_items(
    db: Annotated[Session, Depends(get_readonly_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ContentListResponse:
    return list_visible_news_items(db, user_id=require_user_id(current_user))
```

Structured logging shape:

```python
logger.error(
    "Unable to resolve feed config for content",
    extra={
        "component": "feed_backfill",
        "operation": "resolve_config",
        "item_id": str(content.id),
    },
)
```

Settings shape:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_url: PostgresDsn
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
```

## Tests and Checks

- Add tests for new functionality under `tests/` when you change production behavior.
- Scripts under `scripts/` do not need tests unless the task specifically asks for them.
- If you change the admin CLI, bug-test the touched CLI commands with `pytest tests/admin -v` and `ruff check admin tests/admin` before handoff when possible.
- Run `ruff check` on touched Python files, or the repo, before handoff when possible.
- Use `pytest tests/ -v` for relevant validation when behavior changes.

## Common Commands

```bash
uv sync && . .venv/bin/activate
alembic -c migrations/alembic.ini upgrade head
scripts/dev.sh
ruff check .
ruff format .
pytest tests/ -v
uv run -m admin logs exceptions --limit 20
uv run -m admin logs tail --limit 200
```
