# Architecture Improvement Plan - 2026-04-27

This plan is based on an architecture/source review across backend, queue, metadata, API contracts, iOS, admin/ops, and tests. Treat `docs/architecture.md` as the canonical architecture reference; this document is an execution plan for hardening the current architecture without changing the deployment shape.

## Implementation status

- [x] Add architecture guard command/docs.
- [x] Add production CORS settings.
- [x] Implement Apple JWKS signature verification.
- [x] Replace in-memory admin sessions with signed TTL admin cookies.
- [x] Add queue health query.
- [x] Expose queue health in the admin CLI.
- [x] Add API contract golden fixtures.
- [x] Add metadata accessor module and move selected content response/mapper/submission reads to it.
- [x] Add task spec registry for top task types.
- [x] Validate task payloads at both enqueue and dispatch boundaries.
- [x] Move submission status listing into a query.
- [x] Move mixed search into a query.
- [x] Centralize authenticated user-id extraction for API routers.
- [x] Add content lifecycle transition tests and extract lifecycle helpers.
- [x] Document canonical news read model and legacy report script.
- [x] Add explicit `news_items` to content-card adapter.
- [x] Add grouped settings views and redacted config diagnostics.
- [x] Move queue/worker and storage path consumers to grouped settings views.
- [x] Add content metadata key report script.
- [x] Add admin dashboard queue health partial.
- [x] Add direct query tests for extracted router orchestration.
- [x] Include public contract freshness check in architecture guard.
- [x] Emit structured content lifecycle events from processing and summarization paths.
- [x] Promote provider/model to first-class structured log fields for vendor telemetry.

## Brief architecture readout

Newsly is a well-chosen production monolith. FastAPI owns auth, APIs, admin, chat, discovery, integrations, and processing orchestration; PostgreSQL owns canonical data, async queue state, visibility overlays, and search; SwiftUI/iOS, the Share Extension, Jinja admin UI, and machine clients all depend on the backend as source of truth. That shape is right for the current scale. Do not split this into microservices.

The strongest architectural idea is the split between canonical content, per-user overlays, and per-user feature state. `contents` stores canonical long-form content; `content_status`, read, favorite, unlike, and knowledge-save rows layer user state; chat/discovery/integration state gets dedicated tables. The shared feed query also centralizes visibility rules: completed content, non-skipped classification, digest-only exclusion, and inbox membership for long-form items. Preserve that boundary.

The queue design is also mostly sound for a monolith. It uses Postgres-backed `processing_tasks`, task-to-queue partitioning, active-task dedupe, `pg_notify`, leases, retry scheduling, structured logs, Langfuse task traces, and handler/workflow separation. The weakness is not "DB queue bad"; it is that task payloads, task specs, and content-state transitions are still too implicit for the amount of processing complexity now flowing through the system.

The main pressure point is `content_metadata`. The repo already knows this: `metadata_state.py` explicitly says the current phase is dual-write, preserving top-level keys beside `domain` and `processing` namespaces for legacy readers. That is a healthy migration strategy, but it is currently sitting in the danger zone where old and new shapes coexist and mobile/API response shaping still reads raw dicts in several places.

The other big pressure point is client contract stability. The docs correctly make OpenAPI authoritative and require generated Swift/Go artifacts to be regenerated, not edited manually. But the iOS model layer still has hand-coded DTOs and raw `metadata: [String: AnyCodable]` fields for detail payloads, so backend metadata changes can still leak into product behavior without a contract failure.

## Ranked recommendations

| Rank | Recommendation | Impact | Effort | Risk |
| ---: | --- | --- | --- | --- |
| 1 | Finish the metadata boundary migration, without stripping compatibility fields yet | Very high | M | M |
| 2 | Add typed task payloads and a central task spec registry | Very high | M | M |
| 3 | Consolidate processing status transitions behind one lifecycle module | High | M | M |
| 4 | Strengthen API/iOS contract gates and reduce raw metadata dependence | High | M | L-M |
| 5 | Move remaining router orchestration into commands/queries | High | S-M | L |
| 6 | Make `news_items` the explicit fast-news read model while preserving legacy adapters | High | M | M |
| 7 | Add queue/admin SLO surfaces for backlog age, retries, leases, and provider failures | High | S-M | L |
| 8 | Production-hardening pass: CORS, Apple verification, admin sessions | High | M | M |
| 9 | Tame settings/config sprawl with grouped views and redacted diagnostics | Medium-high | S-M | L |
| 10 | Add architecture regression gates before refactoring deeper | Medium-high | S | L |

## 1. Finish the metadata boundary migration

**Problem.** `content_metadata` currently carries domain data, summaries, image state, feed detection, processing workflow state, submission flags, and share/chat state. The code is already dual-writing top-level compatibility keys plus `domain` and `processing` namespaces, but readers still consume a mix of raw dicts, merged views, and Pydantic models.

**Why it matters.** Over the next 3-6 months, every new feature will be tempted to add another metadata key. That will slow iOS work, create invisible coupling between processing and DTO builders, and make failed/partial processing states harder to reason about.

**Target shape.**

- Keep `content_metadata` as flexible JSON, but make access disciplined.
- `domain`: article/podcast/news source facts and summary payloads.
- `processing`: workflow state, submission flags, dedupe/canonicalization hints, image state, retry/debug breadcrumbs.
- API response builders read through a `ContentMetadataView` or accessor functions, not raw arbitrary keys.
- Existing top-level keys continue to be written for compatibility until all known readers are moved.

**First 1-3 commits.**

1. Add `app/models/metadata_access.py` with typed accessors: `summary()`, `processing_flag()`, `detected_feed()`, `submission_user_id()`, `image_state()`, `news_fields()`.
2. Move `content_responses.py`, `content_mapper.py`, and `content_submission.py` reads to the accessors while preserving existing output.
3. Add `scripts/report_content_metadata_keys.py` to sample production/staging metadata and report unknown top-level keys, unknown processing keys, and keys read by API DTOs.

**Validation/tests.**

- Unit tests for legacy flat metadata, namespaced metadata, and mixed dual-write metadata.
- Existing `tests/routers` and `tests/models` around content list/detail.
- Golden JSON tests for representative article, podcast, news, failed, skipped, awaiting-image, and share-and-chat records.

**Risks/tradeoffs.** Do not strip old keys yet. Temporary duplication is cheaper than breaking iOS or old worker tasks.

## 2. Add typed task payloads and a central task spec registry

**Problem.** Queue routing, dedupe, payload shape, and handler registration are spread across `QueueService`, handlers, workflows, and task models. `QueueService` has a task-to-queue map and active dedupe, while the processor dispatches handlers and wraps task execution with logs/traces. That is good machinery, but the payload contracts are still mostly raw dicts.

**Why it matters.** Processing now includes URL analysis, extraction, summarization, image generation, feed discovery, onboarding, X sync, dig-deeper, news enrichment, and media transcription. Raw payload drift will show up as retry storms, stuck tasks, or silent no-ops.

**Target shape.** Add a small `TaskSpec` registry:

```python
TaskSpec(
    task_type=TaskType.ANALYZE_URL,
    queue=TaskQueue.CONTENT,
    payload_model=AnalyzeUrlPayload,
    handler_key="analyze_url",
    dedupe=Dedupe.by_content_id,
)
```

Keep `ProcessingTask.payload` as JSON. Only validate/normalize at enqueue and dispatch boundaries.

**First 1-3 commits.**

1. Add `app/pipeline/task_specs.py` with specs for `ANALYZE_URL`, `PROCESS_CONTENT`, `SUMMARIZE`, `PROCESS_PODCAST_MEDIA`, and `GENERATE_IMAGE`.
2. Add Pydantic v2 payload models with `extra="allow"` for first-phase compatibility.
3. Make `QueueService.enqueue()` consult the spec for default queue and dedupe behavior, while preserving explicit queue overrides.

**Validation/tests.**

- Queue enqueue tests for default queue, explicit queue override, dedupe reuse, and malformed payload.
- Processor tests proving legacy payloads still dispatch.
- Admin/CLI tests for task status output, because task payload shape is operator-facing.

**Risks/tradeoffs.** Over-strict validation can break old pending tasks. Start permissive, log unknown keys, and only tighten task-by-task.

## 3. Consolidate processing status transitions behind one lifecycle module

**Problem.** Status rules are split across `ContentProcessingWorkflow`, `ContentStatusStateMachine`, `ContentWorker`, summarization handlers, image handlers, and media handlers. The code already has good pieces: workflow transition recording, summarization input fingerprints, `awaiting_image`, image eligibility. The transition authority is not singular.

**Why it matters.** The hardest production bugs in this app will be content stuck in `processing`, duplicate summarize/image tasks, completed items missing generated assets, or failed content still rendering like success.

**Target shape.** One `ContentLifecycle` module owns allowed transitions and post-transition side effects:

- after analysis: enqueue process or terminal skip/fail
- after extraction: enqueue summarize, media, or terminal fail
- after summarize: completed or awaiting image
- after image: completed
- after non-retryable extraction failure: failed with normalized metadata

**First 1-3 commits.**

1. Write transition table tests before changing behavior.
2. Extract decision functions from `ContentWorker.process_content()` into `app/services/content_lifecycle.py`.
3. Replace only the `ContentWorker` calls first; leave handlers untouched until tests prove parity.

**Validation/tests.**

- Article happy path: new -> processing -> summarize -> awaiting_image/completed.
- Podcast path: process -> media/transcribe -> summarize.
- Twitter video path with duration limit and transcript present/missing.
- Canonical URL conflict path.
- Failed extraction path clears success-looking summary/body fields.

**Risks/tradeoffs.** This touches processing behavior, so keep the first pass as extraction of existing logic, not redesign.

## 4. Strengthen API/iOS contract gates and reduce raw metadata dependence

**Problem.** OpenAPI is documented as authoritative and generated Swift/Go artifacts are checked in, which is excellent. But `ContentDetail` on iOS still decodes broad fields, including raw metadata, and backend DTO builders still shape many response fields from metadata.

**Why it matters.** Mobile/API evolution will slow if every metadata refactor risks breaking a screen. The iOS app should depend on stable, typed response fields for product behavior, not raw metadata internals.

**Target shape.**

- Keep `metadata` in `ContentDetailResponse` for compatibility/debugging.
- Treat typed fields such as `summary_kind`, `summary_version`, `structured_summary`, `bullet_points`, `quotes`, `topics`, `body_available`, `image_url`, `news_*`, and `detected_feed` as the actual public contract.
- Add contract fixtures for each major content type and state.
- Make contract diff checks required before merging API changes.

**First 1-3 commits.**

1. Add `tests/contracts/test_content_contract_fixtures.py` with golden outputs.
2. Add fixtures for article, podcast, news item, failed submission, detected feed, awaiting image.
3. Add a CI/script step around `scripts/check_public_contracts.sh` and fail on uncommitted OpenAPI/generated diffs.

**Validation/tests.**

- Backend golden tests.
- Existing `scripts/check_public_contracts.sh`.
- iOS Swift decode tests for the same fixture JSON where practical.
- No public API field removals.

**Risks/tradeoffs.** Golden tests can become noisy if too broad. Keep them focused on mobile-critical DTOs.

## 5. Move remaining router orchestration into commands/queries

**Problem.** Most layering is good, but some routers still orchestrate service calls directly. For example, mixed search combines local content search, feed finding, and podcast search in the route function. Submission listing also contains query construction and response shaping in the router. That violates the repo's intended router -> command/query -> repository/service direction.

**Why it matters.** Route modules become sticky places where business logic accretes. That slows feature work because tests must drive HTTP even when the behavior is really a query/use-case.

**Target shape.**

- Routers validate path/query/body/auth and call one command/query.
- Queries own read orchestration and DTO construction.
- Commands own write orchestration and enqueue side effects.
- Services remain business/integration helpers.

**First 1-3 commits.**

1. Move `/api/content/submissions/list` logic into `app/queries/list_submission_statuses.py`.
2. Move `/api/content/search/mixed` logic into `app/queries/search_mixed.py`.
3. Add a tiny shared `_require_user_id()` helper or dependency to stop duplicating it across routers.

**Validation/tests.**

- Existing router tests should pass with no contract change.
- Add direct unit tests for the new queries.
- Run OpenAPI diff to confirm no route/response change.

**Risks/tradeoffs.** Do not chase every router at once. Move only endpoints you touch for feature work.

## 6. Make `news_items` the explicit fast-news read model while preserving legacy adapters

**Problem.** The app now has both legacy `contents` rows with `content_type=news` and the newer `news_items` table for short-form feed evidence, summaries, clustering, visibility scope, and source metadata. There are also conversion paths for both content/news surfaces.

**Why it matters.** If fast news keeps evolving, dual read models will create confusing bugs: duplicate reads, duplicate conversion-to-article flows, inconsistent discussion endpoints, and mismatched mobile list behavior.

**Target shape.**

- `news_items` is canonical for fast news.
- `contents.news` remains a legacy/compatibility bridge and conversion source until clients move.
- A compatibility adapter emits `ContentSummaryResponse`/`ContentDetailResponse` for old surfaces.
- The architecture doc states which path is canonical.

**First 1-3 commits.**

1. Add `docs/architecture.md` clarification: canonical fast-news source is `news_items`; `contents.news` is compatibility/legacy where applicable.
2. Add a query adapter that maps `NewsItem` to the existing content card DTO without going through fake content metadata.
3. Add a migration/report script showing counts of legacy `contents.news` rows linked/unlinked to `news_items`.

**Validation/tests.**

- Compare `/api/news/items` list behavior against expected card fixtures.
- Conversion-to-article tests for `news_item_id`.
- Read status tests for news item read rows vs content read rows.

**Risks/tradeoffs.** Avoid deleting old content-news paths until the iOS app and CLI are confirmed off them.

## 7. Add queue/admin SLO surfaces

**Problem.** Admin UI and CLI already exist, and logs/traces are present. The missing layer is operational SLO visibility: queue age, stuck leases, retry distribution, per-task failure rates, provider-specific failures, and dedupe reuse rates. The docs already position admin as the operator surface and event/log telemetry as first-class.

**Why it matters.** The next reliability issues will not be "is the server up?" They will be "why are image jobs lagging?", "why did X sync retry 40 times?", "which provider is failing?", and "which task type is starving content processing?"

**Target shape.** Add `QueueHealthSnapshot` with:

- pending count by queue/task type
- oldest pending age by queue/task type
- processing count and expired lease count
- retry bucket counts
- failed count in last N hours
- top failure messages by task type
- optional vendor usage/cost overlay for LLM-heavy tasks

**First 1-3 commits.**

1. Add `app/queries/queue_health.py`.
2. Add `admin health queue` or extend `admin health snapshot`.
3. Add an admin page partial for queue health.

**Validation/tests.**

- Postgres-backed tests with seeded pending/processing/failed tasks.
- Admin CLI tests under `tests/admin`.
- Ensure queries are bounded and indexed.

**Risks/tradeoffs.** Health queries can become expensive. Keep default windows small and avoid scanning large payloads.

## 8. Production-hardening pass: CORS, Apple verification, admin sessions

**Problem.** The architecture doc explicitly calls out wide-open CORS, in-memory admin sessions, and MVP Apple token verification as known constraints. `app/main.py` currently allows all CORS origins/methods/headers, which is fine for local development but not ideal for production.

**Why it matters.** This is an active production app with mobile auth and admin/operator surfaces. These are small, high-leverage hardening changes that reduce operational risk without changing product behavior.

**Target shape.**

- Dev: permissive CORS remains easy.
- Production: explicit CORS origins from settings.
- Apple Sign In: verify JWT signature using Apple JWKS with cache.
- Admin sessions: signed/TTL cookie or DB-backed session table, not process memory.

**First 1-3 commits.**

1. Add `cors_allow_origins` setting; default permissive in development, require explicit in production.
2. Implement Apple JWKS verification with cache and tests for invalid `kid`, invalid signature, expired token.
3. Add DB-backed `admin_sessions` or signed session cookie with TTL; preserve current login UI.

**Validation/tests.**

- Auth unit tests.
- Admin login/logout tests.
- Production settings smoke test.
- Manual check against local iOS auth flow.

**Risks/tradeoffs.** Biggest risk is locking out admin or breaking Apple auth. Gate changes behind production/dev settings and keep rollback simple.

## 9. Tame settings/config sprawl with grouped views and redacted diagnostics

**Problem.** `Settings` is the single config authority, which is correct, but it now spans database, auth, worker limits, LLM providers, tracing, discovery, podcasts, X, PDFs, Whisper, HTTP, storage, crawl4ai, Firecrawl, sandboxing, and more.

**Why it matters.** As providers and workers grow, every change must answer: which env vars are required, which are production-only, which are optional, and which are configured incorrectly?

**Target shape.**

Keep env names exactly as-is. Add grouped read-only views:

- `settings.queue`
- `settings.auth`
- `settings.storage`
- `settings.providers`
- `settings.discovery`
- `settings.integrations.x`
- `settings.observability`

Add redacted config diagnostics for admin/CLI: configured/missing, not secret values.

**First 1-3 commits.**

1. Add grouped property models without changing env parsing.
2. Move queue code and storage path code to use grouped views.
3. Add `admin health config --output json` or extend health snapshot.

**Validation/tests.**

- Settings tests for env aliases and defaults.
- Redaction tests for all secret-like fields.
- No runtime env var rename.

**Risks/tradeoffs.** The trap is over-refactoring `Settings`. Keep this as additive grouping, not a nested-env migration.

## 10. Add architecture regression gates before deeper refactors

**Problem.** The test suite is broad and uses isolated Postgres schemas, FastAPI `TestClient`, and fixture-driven samples. That is a strong base. But the risky upcoming work crosses architecture seams: metadata compatibility, task lifecycle, OpenAPI contracts, iOS decoding, and admin ops.

**Why it matters.** The right way to move fast here is not to write more abstractions first; it is to freeze the key behaviors, then refactor safely.

**Target shape.** A small "architecture guard" test set:

- contract fixtures
- metadata compatibility fixtures
- queue lifecycle tests
- content lifecycle transition tests
- admin CLI health tests
- OpenAPI/generated artifact check

**First 1-3 commits.**

1. Add `tests/contracts/` with DTO golden fixtures.
2. Add `tests/pipeline/test_task_specs.py` and `tests/services/test_content_lifecycle.py`.
3. Add a documented `make`/script target or README section for "architecture guard" checks.

**Validation/tests.**

- `pytest tests/contracts tests/models tests/pipeline tests/routers -v`
- `scripts/check_public_contracts.sh`
- `pytest tests/admin -v` when touching admin CLI.

**Risks/tradeoffs.** Too many golden tests can slow iteration. Keep only high-value mobile/API states.

## Likely false positives

Do not churn these areas just because they look imperfect:

1. **Do not split into microservices.** The current monolith is the right deployment unit. The queue and providers need stronger internal contracts, not network boundaries.
2. **Do not replace the DB-backed queue yet.** Postgres queueing is appropriate for the product stage. Improve task typing, leases, SLOs, and admin visibility before considering an external broker.
3. **Do not replace Jinja admin with a frontend app.** The admin UI is intentionally simple and server-rendered. That is a feature, not a weakness.
4. **Do not remove JSON metadata wholesale.** Flexible metadata is useful for heterogeneous article/podcast/news/extraction payloads. The issue is uncontrolled access, not JSON itself.
5. **Do not abandon OpenAPI-generated clients.** The checked-in generated Swift/Go artifacts and regeneration scripts are a strong pattern. Tighten the gates; do not switch contract systems.
6. **Do not over-abstract every service.** The existing gateway package for HTTP/LLM/queue seams is enough. Add contracts where volatility is real: task payloads, metadata access, provider calls.
7. **Do not flatten the per-user overlay model.** The split between shared content and user-specific visibility/read/favorite state is one of the healthiest parts of the architecture.

## Two-week execution plan

### Week 1: guardrails and low-risk seams

**Commit 1 - Add architecture guard command/docs**

- Status: complete.
- Add a documented validation target for contract, metadata, queue, and router checks.
- No behavior change.
- Run: `ruff check` on touched files, targeted `pytest`.

**Commit 2 - Add API contract golden fixtures**

- Add golden JSON tests for `ContentSummaryResponse` and `ContentDetailResponse`.
- Cover article, podcast, news, failed/skipped, detected feed, and awaiting image.

**Commit 3 - Add metadata accessor module**

- Status: complete.
- Add `app/models/metadata_access.py`.
- Add tests for flat, namespaced, and dual-write metadata.
- No production behavior change.

**Commit 4 - Move content response builders to metadata accessors**

- Status: complete for the first response-builder/mapper/submission pass.
- Touch `app/routers/api/content_responses.py`.
- Preserve output exactly; golden tests should prove no drift.

**Commit 5 - Add task spec registry for top task types**

- Status: complete.
- Add permissive Pydantic payload models for the most common tasks.
- Keep raw JSON storage.
- Validate at enqueue/dispatch with compatibility fallback.

**Commit 6 - Add queue health query**

- Status: complete.
- Add `app/queries/queue_health.py`.
- Seed tests for backlog age, retry buckets, expired leases, and failures.

**Commit 7 - Expose queue health in admin CLI**

- Status: complete.
- Add/extend `admin health snapshot` or add `admin health queue`.
- Run `pytest tests/admin -v`.

### Week 2: behavior-preserving refactors and production hardening

**Commit 8 - Move submission status listing into a query**

- Status: complete.
- Create `app/queries/list_submission_statuses.py`.
- Router becomes validation/auth only.
- Contract tests prove no response change.

**Commit 9 - Move mixed search into a query**

- Status: complete.
- Create `app/queries/search_mixed.py`.
- Router calls query only.
- Add direct query tests with stubbed external feed/podcast search.

**Commit 10 - Add content lifecycle transition tests**

- Freeze current transition behavior before extraction.
- Cover article, podcast, tweet video, summarize reuse, image awaiting/completion, and failed extraction.

**Commit 11 - Extract lifecycle decision helpers**

- Move decision logic from `ContentWorker` into `app/services/content_lifecycle.py`.
- No new states, no API changes.

**Commit 12 - Add production CORS settings**

- Status: complete.
- Dev keeps permissive default.
- Production requires explicit allowlist.
- Add settings tests and app middleware tests.

**Commit 13 - Add Apple token verification tests and verifier skeleton**

- Status: complete.
- Implement JWKS cache and signature verification.
- Add failure-case tests.
- Keep rollout controlled with settings if needed.

**Commit 14 - Document canonical news read model**

- Status: complete.
- Update `docs/architecture.md`.
- Add a migration/report script for legacy `contents.news` linkage to `news_items`.
- No deletion, no endpoint break.

## Tests, metrics, and observability

**Tests to add first**

- `tests/contracts/test_content_api_fixtures.py`
- `tests/models/test_metadata_access.py`
- `tests/pipeline/test_task_specs.py`
- `tests/services/test_content_lifecycle.py`
- `tests/queries/test_queue_health.py`
- `tests/queries/test_search_mixed.py`
- iOS decode tests for backend fixture JSON where the client still uses hand-coded models.

**Metrics/SLOs to expose**

- Queue pending count by `queue_name` and `task_type`.
- Oldest pending age by `queue_name` and `task_type`.
- Processing task count and expired lease count.
- Retry bucket distribution.
- Failure count/rate by task type over recent windows.
- Top error messages by task type.
- LLM/provider usage and cost by feature/task.
- Content lifecycle counts by status: `new`, `processing`, `awaiting_image`, `completed`, `failed`, `skipped`.
- Metadata validation warning count by content type and summary kind.
- API validation error count by route and client surface.

**Observability additions**

- Status: complete for queue task context and vendor telemetry logs.
- Add `request_id`, `task_id`, `content_id`, `user_id`, `queue_name`, `task_type`, `provider`, and `model` consistently to task/provider logs.
- Status: complete for content lifecycle events emitted from `ContentWorker` and the summarize handler.
- Emit explicit lifecycle events: `content.extracted`, `content.summarize_queued`, `content.summary_completed`, `content.image_queued`, `content.completed`, `content.failed`.
- Add admin CLI JSON output for queue health so Codex/operator workflows can inspect production without scraping logs.

## Questions before phase 2

1. Which clients besides the SwiftUI app, Share Extension, and Go CLI are consuming the public API and must be treated as compatibility-critical?
2. Should `news_items` become the canonical fast-news source for all new product work, with `contents.news` treated as legacy compatibility?
3. Which raw `metadata` keys does the iOS app still rely on for UI behavior, not just debugging?
4. What are the real production worker counts and queue partitions currently running in Docker: content, media, image, onboarding, twitter, chat?
5. For production hardening, should admin sessions use signed stateless cookies or DB-backed sessions with TTL/audit rows?
