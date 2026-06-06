# Newsly Agent Guide

Start with this file. Open [docs/architecture.md](docs/architecture.md) only when a task crosses package boundaries, changes data/API contracts, touches workers/queues, or needs system-level context.

Use [docs/codebase/](docs/codebase/) only for the specific folder or surface you are modifying.

Use [docs/coding-guidelines.md](docs/coding-guidelines.md) for local code patterns, test expectations, and common commands.

This file stays minimal and only captures repo-specific working rules.

## Core Rules

- Never commit or push unless explicitly asked.
- Prefer small, local changes that follow the existing layer boundaries.
- Keep durable architecture notes in `docs/architecture.md`; keep this file limited to agent operating rules and routing.

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
