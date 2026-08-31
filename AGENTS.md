# Newsly Agent Guide

Start with this file. Open [docs/architecture.md](docs/architecture.md) only when a task crosses package boundaries, changes data/API contracts, touches workers/queues, or needs system-level context.

Use [docs/laws/](docs/laws/) for canonical product behavior and invariants. Update the relevant law whenever intended behavior changes.

Use [docs/coding-guidelines.md](docs/coding-guidelines.md) for local code patterns, test expectations, and common commands.

This file stays minimal and only captures repo-specific working rules.

## Core Rules

- Never commit or push unless explicitly asked.
- Prefer small, local changes that follow the existing layer boundaries.
- Choose the simplest implementation that fully meets the current requirements. Avoid speculative abstractions, configuration, and indirection.
- Build changes as small, end-to-end working slices. Each increment must leave the affected product path functional and verifiable.
- Prefer durable designs aligned with the intended architecture. Remove obsolete internal paths instead of adding fallbacks or compatibility layers. When an active external contract or staged migration requires compatibility, define the canonical owner and removal condition.
- Keep `docs/log.md` current while implementing. Record concise, dated entries with the branch, scope, decisions, validation, and unfinished work; preserve unrelated entries.
- Keep durable architecture notes in `docs/architecture.md`; keep this file limited to agent operating rules and routing.
- Keep `docs/laws/` behavioral rather than structural: state what must remain true, not which files currently implement it.

## Working Shape

- Backend: Rust 1.94 modular monolith using Axum, Tokio, Tower, SQLx 0.9, and a
  PostgreSQL-backed durable task queue.
- Agent and provider runtime: Rig behind Newsly-owned interfaces, with
  `async-openai` or typed direct HTTP for provider-native operations. Direct E2B
  transport lives in Rust; no Python or Node gateway owns sandboxes.
- Python is limited to `python/document_extractor`, the database-free Crawl4AI
  extraction process, and `python/evals`, the offline model/evaluation package.
  Neither package is an application backend or schema owner.
- Clients: SwiftUI iOS app, iOS Share Extension, server-rendered Rust admin UI,
  and machine-facing APIs.
- Native UI automation and reference images live under
  `client/newsly/Maestro/flows/` and `client/newsly/Maestro/baselines/`.
- UI note: this repo is not a React app. Web UI is server-rendered; mobile UI is SwiftUI.
- Runtime note: local development should use the normal local services and a local PostgreSQL instance. Treat Docker as a staging/production runtime, not the default local-dev path.
- Operator note: use `newsly-admin` for ownership, health, task, usage, and eval
  operations, and the Rust admin HTTP surface or container runtime for logs.
  Retired Python operator entrypoints exist only in repository history and must
  not be recreated.

## Dependency Direction

For backend changes, follow this order:

1. Axum routes and public contracts
2. application commands/queries or task executors
3. domain and gateway traits
4. SQLx/provider/infrastructure adapters

For processing changes, follow this order:

1. task contract and executor
2. immutable preparation and fenced finalization
3. strategy or provider adapter
4. SQLx persistence and public-state projection

## Workflows

Backend change:

1. Find the owning Axum route and `newsly-contracts` request/response types.
2. Put orchestration in commands/queries, not route handlers.
3. Keep SQL in `newsly-db` and external calls in provider/gateway crates.
4. Add focused Rust tests, including PostgreSQL integration coverage when persistence changes.
5. Run `cargo fmt`, warning-denied Clippy, focused tests, and contract drift checks.

Processing change:

1. Identify the task type, handler, and queue ownership.
2. Trace persistence and retry semantics before changing provider code.
3. Keep every transaction short: prepare immutable input, release the connection,
   run external work, then finalize through a fresh exact-lease transaction.
4. Keep strategy/provider code focused on extraction, transformation, or external calls.
5. Add tests for success, malformed input, cancellation, lease loss, and retry/failure behavior when production behavior changes.

Python-island change:

1. Work only within `python/document_extractor` or `python/evals`.
2. Preserve the versioned language-neutral boundary in `contracts/`.
3. Do not add PostgreSQL, queue, auth, migration, or product-state ownership to Python.
4. Run Ruff, MyPy, and the focused package tests.

Production debug:

1. Confirm whether the user is testing production or local.
2. Use `newsly-admin` for health, queue/task, usage, and ownership evidence.
3. Use the Rust admin log surface or container runtime only after narrowing the symptom.
4. Prefer DB, log, runtime, and queue evidence over local speculation.
5. Do not patch production directly unless explicitly asked.

Production state sync:

- Use `scripts/sync_production_state.sh` as the only supported production-to-local
  sync entrypoint. It replaces the local database, copies recent file-backed
  assets, and restarts only the Rust API. Do not add a database-only copy script
  or start local workers as part of a state sync.

## Code Rules

- Prefer free functions and small data types; introduce traits only at a real runtime boundary or test seam.
- Keep components modular and concerns clearly separated.
- Check the capabilities, documentation, and types of existing dependencies before writing custom code or adding a package.
- Prefer established, well-maintained libraries when they reduce overall complexity or improve reliability; do not reimplement common functionality without a clear reason.
- Use Serde for wire/storage encoding, Utoipa for public OpenAPI, Schemars for
  tool/model schemas, and explicit validation/conversion at boundaries.
- Keep public, domain, persistence, and provider SDK types distinct. Never persist Rig,
  `async-openai`, ConnectRPC, or E2B SDK-native objects.
- Model missing, explicit `null`, defaults, UTC timestamps, open enums, and
  discriminated unions deliberately; do not rely on incidental Serde behavior.
- Favor guard clauses and straightforward control flow over nested branches.
- Use normal Rust naming and formatting conventions; do not silence warnings globally.
- Do not hardcode secrets; load validated configuration at process startup and redact diagnostics.
- Use SQLx parameter binding and checked queries; never interpolate untrusted SQL.
- Emit structured `tracing` fields and stable error codes at process boundaries.
