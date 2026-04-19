# Newsly Agent Guide

Read [docs/architecture.md](docs/architecture.md) first. It is the canonical reference for system architecture, package ownership, data model, API surface, workers, scrapers, iOS, admin UI, operations, and testing.

Use [docs/codebase/](docs/codebase/) for folder-level reference when you need implementation orientation.

This file stays minimal and only captures repo-specific working rules.

## Core Rules

- Keep replies short, technical, and complete.
- Never commit or push unless explicitly asked.
- If asked to commit, commit to the current checked-out branch unless explicitly asked to create or use a different branch. This applies even if the current branch is `main`.
- Prefer small, local changes that follow the existing layer boundaries.
- Do not duplicate architecture notes here; update `docs/architecture.md` instead.

## Working Shape

- Backend: FastAPI, SQLAlchemy 2, Pydantic v2, database-backed async task queue.
- Clients: SwiftUI iOS app, iOS Share Extension, Jinja admin UI, machine-facing APIs.
- UI note: this repo is not a React app. Web UI is Jinja-rendered; mobile UI is SwiftUI.
- Runtime note: local development should use the normal local services and a local PostgreSQL instance. Treat Docker as a staging/production runtime, not the default local-dev path.
- Operator note: use the `admin` CLI for Docker-runtime inspection and repairs. `admin logs tail` defaults to the unified `newsly` container log stream.

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

## Code Rules

- Prefer functions over classes unless stateful objects clearly improve the design.
- Use full type hints and validate boundary inputs with Pydantic v2.
- Favor guard clauses and straightforward control flow over nested branches.
- Use `lower_snake_case` for Python names and UPPER_CASE for constants.
- Do not hardcode secrets; keep config in `app/core/settings.py`.
- Use parameterized DB access, never SQL built with f-strings.
- Log errors with `logger.error()` or `logger.exception()` and structured `extra` fields.

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
```
