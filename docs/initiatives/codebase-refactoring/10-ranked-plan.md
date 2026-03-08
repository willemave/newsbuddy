# Refactoring Plan (Ranked)

This plan is based on full folder-level review docs in `docs/research/codebase/**/README.md` plus direct inspection of key implementation files.

## Ranking Criteria
- Production risk reduction
- Architectural simplification and long-term maintainability
- Interface/type consistency across backend and iOS
- Refactor cost vs. impact

## Execution Status (February 21, 2026)
- Milestone A completed: canonical backend enum contracts added, API models normalized to enums, OpenAPI export + generated iOS contracts wired.
- Milestone B completed: analyze/process workflow orchestration extracted, metadata state helpers added and used in converters/pipeline flows.
- Milestone C completed: gateway layer introduced (HTTP/LLM/queue), core pipeline/router call paths migrated, shared feed query builder adopted across list/favorites/recently-read/search.
- Milestone D completed: iOS cursor pagination base viewmodel added and adopted by legacy lists, API client request pipeline unified, deploy script surface consolidated with shared helpers + operations command index.
- Cross-cutting completion: duplicate test modules removed, module-size guardrails added, and targeted backend/iOS build validation executed.

## 1) Canonical Domain Contracts and Type System (Highest)
Why this is first:
- The same concepts (`content_type`, `status`, `summary_kind`, `summary_version`) are represented as raw strings in many places (`app/models/schema.py`, `app/routers/api/models.py`, iOS models/services), causing drift and branching logic.

Actions:
- Promote `ContentType`, `ContentStatus`, `ContentClassification`, `TaskType`, `TaskStatus` to canonical API-facing enums everywhere.
- Replace string fields in API response models with enums where feasible.
- Introduce one canonical summary contract model layer for all summary kinds; keep versioned adapters only at boundaries.
- Generate iOS API models from OpenAPI (or a checked-in schema) to remove manual drift.

Success criteria:
- No raw string comparisons for core domain enums in routers/presenters/iOS networking layer.
- iOS decoding failures from schema drift reduced to near zero.
- Summary type/version branching centralized into one adapter module.

## 2) Replace Ad-Hoc Pipeline Branching with Explicit Workflow Engine
Why this is second:
- `app/pipeline/handlers/analyze_url.py` and `app/pipeline/worker.py` contain large branching flows that mix orchestration, persistence, and feature-specific logic.

Actions:
- Model ingestion lifecycle as explicit workflow states and transitions (analyze, enrich, process, summarize, image, complete/failed/skipped).
- Move platform-specific branches (Twitter share, feed-subscribe, YouTube/podcast nuances) into pluggable workflow steps.
- Keep handlers thin: parse envelope, load aggregate, invoke workflow step, persist events/state transitions.

Success criteria:
- `analyze_url.py` and `worker.py` reduced to orchestration shells.
- New content source support added via step plugins, not edits across multiple handlers.
- Clear transition map documented and testable.

## 3) Split `content_metadata` into Typed Data + Processing State
Why this is third:
- `content_metadata` currently stores domain data, summary payloads, workflow flags, error context, and feed-subscription markers in one JSON blob.

Actions:
- Separate metadata into:
  - typed content payload (`article_metadata`, `podcast_metadata`, `news_metadata`)
  - summary payload with explicit kind/version
  - processing/runtime metadata (`processing_state`, `errors`, transient flags)
- Introduce migration path and adapters to read legacy blobs.
- Keep `Content` ORM validation lightweight; move heavy validation to boundary/services.

Success criteria:
- Fewer `dict.get(...)` checks and runtime shape guessing.
- Domain model conversion no longer mutates or infers summary fields in multiple places.
- Easier SQL filtering/reporting on processing vs domain fields.

## 4) Consolidate Infrastructure Adapters (HTTP, LLM, Queue)
Why this is fourth:
- There are overlapping transport abstractions (`app/services/http.py`, `app/http_client/robust_http_client.py`, direct `httpx` usage in analyzers/services) and repeated LLM setup paths.

Actions:
- Define explicit gateway interfaces:
  - `HttpGateway` (timeouts/retries/circuit-breaker policy in one place)
  - `LlmGateway` (provider/model resolution, retry/fallback policy, tracing)
  - `TaskQueueGateway` (enqueue/dequeue/retry/ack abstraction)
- Route all callsites through these gateways.
- Move provider fallback/error classification into reusable policy objects.

Success criteria:
- No direct `httpx.Client(...)` creation outside gateway modules.
- One place to change retry/circuit-breaker behavior.
- LLM config and fallback logic removed from business services.

## 5) Unify Content List/Query Assembly and Response Building
Why this is fifth:
- Query/filter/pagination logic is repeated across list/favorites/read-status endpoints, while presenters do mixed formatting and domain checks.

Actions:
- Create one query builder for user-visible content feeds (filters, cursor, read/favorite state).
- Keep endpoint modules focused on HTTP concerns only.
- Move response shaping into a single mapping layer with stable DTO contracts.

Success criteria:
- Reduced duplicated SQL/filter code across routers.
- Consistent pagination/filter semantics across endpoints.
- Smaller router modules with clearer responsibilities.

## 6) iOS List Architecture Convergence
Why this is sixth:
- Both `BaseContentListViewModel` and legacy `ContentListViewModel`/`NewsGroupViewModel` maintain pagination/read/favorite state separately.

Actions:
- Converge list experiences onto one base pagination/state machine.
- Move endpoint-specific differences into strategy/config objects.
- Standardize repository/service interfaces and remove one-off request wrappers.

Success criteria:
- Single reusable pagination flow for long-form/news/favorites/recently-read.
- Lower bug surface for load-more/read-filter behavior.
- Fewer duplicated viewmodel tests and state edge cases.

## 7) API Client Simplification on iOS
Why this is seventh:
- `APIClient` duplicates auth-refresh/error handling across `request`, `requestVoid`, and `requestRaw`.

Actions:
- Introduce one request execution pipeline with generic decoding strategy.
- Keep refresh/auth-failure policy in one function.
- Add typed endpoint descriptors to avoid stringly endpoint usage.

Success criteria:
- Shared auth retry logic with one code path.
- Easier instrumentation and request tracing.
- Fewer networking regressions from inconsistent error handling.

## 8) Test Topology Consolidation
Why this is eighth:
- Both `app/tests` and `tests` contain overlapping suites/fixtures; `pyproject.toml` points at `tests` only while instructions reference `app/tests`.

Actions:
- Choose one canonical test root (`tests/` recommended), migrate duplicates, and delete bridge indirection.
- Consolidate fixtures and remove duplicate modules with identical intent.
- Align `pytest` config and docs with actual layout.

Success criteria:
- One authoritative test tree and fixture source.
- No duplicate test modules for same behavior.
- Faster and more predictable CI test runs.

## 9) File/Module Size Reduction for High-Churn Hotspots
Why this is ninth:
- Multiple files exceed practical maintainability thresholds (`app/services/onboarding.py`, `app/models/metadata.py`, `app/routers/api/models.py`, iOS large views/viewmodels).

Actions:
- Split by feature slice and interface boundary, not arbitrary line counts.
- Introduce focused modules for prompting, schema variants, and UI component families.
- Enforce lightweight module-size and complexity guardrails in CI.

Success criteria:
- Critical modules split into coherent submodules with clear ownership.
- Review and onboarding time reduced for hot files.

## 10) Deployment/Script Surface Rationalization
Why this is tenth:
- Operational scripts are numerous and partially overlapping; deployment logic is spread across shell scripts + supervisor + cron + Docker defaults.

Actions:
- Group scripts by bounded domains (`ops/deploy`, `ops/diagnostics`, `ops/data`).
- Extract common shell helpers for SSH/rsync/logging.
- Add a single operator entrypoint doc + command index.

Success criteria:
- Lower operational drift and fewer one-off script paths.
- Faster incident response and easier automation.

## Suggested Execution Sequence
1. Canonical contracts + enum/type normalization (backend + OpenAPI + iOS generation)
2. Workflow engine extraction from analyze/process handlers
3. Metadata separation migrations + adapters
4. Gateway consolidation (HTTP/LLM/Queue)
5. Router/query unification
6. iOS list architecture convergence
7. iOS API client simplification
8. Test topology consolidation
9. Module splitting and guardrails
10. Ops/script rationalization

## Delivery Strategy
- Run as 4 milestones to control risk:
  - Milestone A: contracts/types + iOS generation + test layout decisions
  - Milestone B: pipeline/workflow + metadata separation
  - Milestone C: gateway consolidation + router/query unification
  - Milestone D: iOS architecture convergence + operational cleanup
