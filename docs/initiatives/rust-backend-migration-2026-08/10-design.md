# Rust Backend Migration Design

Date: 2026-08-30. Status: approved migration design. The implementation is now present in the
local working tree; consolidated validation, release, SQLx production adoption, and deployment
remain outstanding. See [`20-implementation-plan.md`](20-implementation-plan.md) for the executed
work packages and [`30-summary.md`](30-summary.md) for current authority and release status.

This document records the target architecture and the constraints used during implementation.
Its coexistence and phase sections deliberately retain the planned tense as a migration record;
[`30-summary.md`](30-summary.md) is authoritative for current local and production status. This
design does not by itself assert that a local change was validated, released, or applied to
production.

## 1. Decision Summary

Newsly will migrate from the Python backend to a Rust modular monolith through a schema-first
strangler migration.

The recorded decisions are:

1. Rust becomes the owner of the HTTP API, domain logic, PostgreSQL access, schema migrations,
   durable queue, workers, scheduler, authentication, provider integrations, agents, E2B, admin
   surfaces, and operational commands.
2. Production Python remains only as a database-free document-extraction service containing
   Crawl4AI and the extraction policy around it. Python also remains for offline eval/model
   pipelines, including local embedding experiments, datasets, judges, and reports.
3. E2B moves fully into Rust. This includes sandbox creation and reconnection, command streaming,
   file transfer, network policy, snapshots, corpus hydration, lifecycle ownership, diagnostics,
   and cleanup. There will be no permanent Python or Node gateway between Rust and E2B.
4. SQLx is the single Rust PostgreSQL library for both application SQL and migrations. Alembic is
   frozen at an audited cutover head and retired as an active migration tool after one SQLx
   baseline adoption.
5. PostgreSQL remains the durable database and task queue. This migration does not introduce
   Kafka, Redis, Temporal, an external vector database, or service-per-domain infrastructure.
6. Existing Pydantic contracts, generated Swift/Go contracts, JSON Schemas, and golden fixtures
   bootstrap the language-neutral contract corpus. Rust becomes the sole public contract source
   at final authority cutover.
7. Rig is the initial agent execution-engine candidate, hidden behind Newsly-owned interfaces.
   `async-openai` and direct typed HTTP remain available for provider-native capabilities. No SDK
   type may become a durable database or public API type.
8. Known defects and accidental compatibility paths are not parity targets. They are classified as
   correctness prerequisites, intentional contract redesigns, or telemetry-gated retirement work
   before equivalent Rust code is allowed to ship.
9. Every route, queue task, durable namespace, and mutation has exactly one runtime owner at a
   time. Read shadowing is allowed. Dual writes, dual task claims, and dual E2B namespace use are
   forbidden.

This supersedes the earlier tentative ideas of retaining an E2B Python gateway and leaving
Alembic authoritative until the very end. [`docs/architecture.md`](../../architecture.md) records
the implemented end state, while product laws remain the behavioral authority. Whether that state
has reached production is tracked separately from architecture and local implementation.

## 2. Checked Baseline

The migration starts from a large but well-typed modular monolith:

- approximately 506 Python files and 111,000 lines under `app/`;
- approximately 381 Python test files and 97,000 test lines;
- 146 checked-in HTTP operations and 217 OpenAPI component schemas;
- approximately 43 PostgreSQL tables and 91 Alembic revisions;
- approximately 26 background task types across 10 queue partitions;
- 46 reviewed public enum specifications and 103 reviewed public model specifications;
- generated Swift and Go wire contracts with checked-in fixtures and drift checks.

The 2026-08-30 audit reported a green nonvisual backend and native-client baseline, signed Share
Extension success, and passing static/contract/migration checks, while the checkout was actively
changing. Those counts are historical evidence, not acceptance evidence for a future migration
commit. Relevant audit seams were rechecked in the current worktree, and four release blockers are
still structurally present:

1. `RUN_LLM_TASK` and image/agent workflows can retain an ORM session and implicitly opened
   transaction across long external calls.
2. missing or inaccessible `Content` detail can be retried as an unrelated `NewsItem` with the same
   integer identifier.
3. the production deployment workflow has no repository-enforced test job on which deployment
   depends;
4. the typed-response contract test can pass while examining zero effective `/api/` routes because
   the installed FastAPI routing shape stores included routers behind non-`APIRoute` wrappers.

The checked-in OpenAPI document is valuable, but it is not sufficient by itself until the vacuous
coverage gate is repaired and wire-presence inconsistencies are inventoried.

The supplied validation snapshot recorded 2,891 passing nonvisual backend tests, 624/624 passing
nonvisual native iOS tests, all four signed Share Extension modes reaching the live local API and
queue, the original 15 AXe paths plus four extension reruns, successful signed app/extension/test
bundle builds, and passing Ruff, MyPy, Go, contract-generation, architecture, fresh-migration, and
single-head checks. Four Python failures and one iOS assertion belonged to concurrent visual-token
work; personalized onboarding still needed a rerun after its new intro screen. The temporary audit
database was removed and no source, production, or distribution mutation occurred. This is useful
pre-migration evidence, but every implementation package must produce fresh evidence from one
settled SHA.

## 3. Goals

1. Replace FastAPI, SQLAlchemy, Pydantic-AI, Alembic, and the general Python worker runtime with a
   coherent Rust backend.
2. Keep installed iOS clients and the Go CLI compatible except where a separately planned API
   correction is intentionally released.
3. Preserve product laws, database state, retry semantics, provider routing/privacy, usage
   accounting, and operational rollback.
4. Make transaction ownership explicit and ensure external calls never hold application database
   transactions or scarce pool connections.
5. Use the existing type system to build stronger Rust wire and domain types rather than
   translating untyped dictionaries or ORM-shaped objects.
6. Preserve the PostgreSQL queue's exact lease-token, retry-generation, deferral, notification,
   and stale-finalization behavior.
7. Make E2B a direct, observable, fenced Rust provider integration without weakening sandbox or
   host-authority invariants.
8. Keep Python embedding and model experimentation productive while ensuring promotion tests call
   the actual Rust production algorithm.
9. Reduce the system before porting it: delete verified internal dead code, consolidate duplicate
   workflows, and retire public compatibility only after telemetry and client proof.
10. Produce small vertical cutovers with independent canary, rollback, and deletion conditions.

## 4. Non-Goals

- No big-bang rewrite or coordinated one-day database/API/client cutover.
- No simultaneous redesign of the PostgreSQL data model merely to make it look Rust-native.
- No microservice decomposition. The retained Crawl4AI process is an explicit language/runtime
  boundary, not the beginning of service-per-feature architecture.
- No new workflow engine or second queue alongside `processing_tasks`.
- No PyO3 embedding of the backend Python environment into Rust.
- No automatic translation of Python classes or business logic into Rust.
- No persistence of Rig, `async-openai`, ConnectRPC, or E2B-generated SDK objects.
- No public endpoint deletion based only on static caller search.
- No attempt to rewrite arbitrary tools available inside an E2B sandbox. Python, Node, Chromium,
  and Playwright may remain sandbox workload capabilities; Rust owns their Newsly lifecycle and
  transport.
- No requirement to reimplement a third-party executable purely for language purity during the
  first cutovers. Any production Python executable outside the crawler, such as `yt-dlp` or local
  Whisper, is transitional debt with an explicit removal decision and cannot become an unnamed
  permanent island.

## 5. Correctness Invariants

These rules apply to both migration code and the final Rust system:

1. A public route, mutation, queue task, chat session, and E2B namespace has one active runtime
   owner.
2. A database transaction is used only for a bounded state transition. No provider, model, image,
   storage, E2B, crawler, or other unbounded network call occurs while it is open.
3. A worker follows `prepare transaction -> immutable work plan -> external execution -> fresh
   fenced finalize transaction`.
4. External work may be repeated only when its idempotency contract permits it. Ambiguous E2B
   command delivery is never blindly started again.
5. Queue claim, renewal, deferral, retry, and finalization remain compare-and-set transitions using
   the exact owner, lease token, retry generation, and unexpired lease.
6. Losing a queue or chat generation fence prevents durable publication, even if external work
   finishes successfully.
7. Python extraction has no Newsly database, queue, JWT, or mutation authority.
8. The VM has no Newsly or provider credentials. The Rust host owns authentication, database
   state, queues, provider calls, corpus rendering, validation, and product mutations.
9. Open/closed enum policy, absent/null distinction, explicit lenience, and UTC serialization are
   part of the contract, not incidental serializer behavior.
10. Migrations have one owner. After SQLx adoption, Alembic history is immutable and no new
    Alembic revision is permitted.
11. Production schema changes remain compatible with the previously active blue/green API slot
    until that slot and every old worker are retired.
12. Provider and agent SDK upgrades must pass live Newsly-contract canaries before release.

## 6. Options Considered

### Option A: schema-first strangler — selected

Run Rust and Python beside the same PostgreSQL database, route explicit endpoint groups to one
owner, and transfer queue task/namespace ownership in bounded slices. This has the longest
coexistence period but the best contract proof, rollback, and production safety.

### Option B: worker-first migration

Port queue execution before the HTTP surface. This promises earlier throughput improvements but
starts at the hardest lease, transaction, agent, and provider boundary and gets less immediate
value from the existing public-contract system.

### Option C: big-bang replacement

Rebuild the whole backend and switch clients and workers together. This has the shortest nominal
coexistence and the highest probability of semantic queue, authentication, JSONB, and persisted
chat-history regressions. It is rejected.

## 7. Target Topology

```text
iOS / Share Extension / Go CLI / Admin
                  |
                  v
         route ownership gateway
             /               \
            v                 v
     Rust Axum API      legacy FastAPI
            |           temporary and shrinking
            +---------+-------+
                      |
                      v
                 PostgreSQL
            state + durable queue
                      ^
                      |
       Rust workers / scheduler / admin / newsly-db
          |           |              |
          |           |              +--> E2B control + envd streams
          |           +-----------------> model/provider APIs
          +-----------------------------> Python document extractor
                                          DB-less Crawl4AI policy service

Python eval/model pipelines
          |
          +--> versioned JSON/NDJSON --> Rust eval driver and real algorithms
```

The final production topology contains Rust application binaries, PostgreSQL, and the Python
document extractor. Offline Python eval environments are not deployed with production images.

## 8. Simplify Before Translation

The migration must not encode the current source tree as the target architecture. Simplification
is divided into three Phase 0 lanes and one deliberate contract-redesign lane.

### 8.1 Phase 0A: correctness and release prerequisites

These are current-system fixes. They precede Rust parity assertions because matching the current
bug would be harmful.

#### Short database transaction ownership

Refactor every LLM, image, E2B, storage, and other long external workflow into:

```text
prepare transaction
  validate owner and state
  reserve attempt/generation
  load required records
  serialize immutable WorkPlan
  commit

external phase
  no ORM entity, Session, Transaction, or checked-out DB connection
  provider/E2B/storage calls
  bounded progress through independent short writes

finalize transaction
  reload and lock canonical rows
  prove queue lease + attempt/generation + product state
  publish or discard outcome atomically
```

Fix the Python implementation before another release. Add a PostgreSQL regression that holds an
external call open while proving `CREATE INDEX CONCURRENTLY`, unrelated writes, and queue
heartbeats are not blocked. `expire_on_commit=False` is not an acceptable fix because it hides ORM
refresh behavior rather than removing transaction ownership.

Rust enforces the same shape structurally: provider crates do not depend on `newsly-db`; work-plan
types contain owned data; a SQLx `Transaction` cannot be stored in a work plan or passed into an
external adapter; progress and lease renewal use separate bounded operations.

#### Separate Content and News identity

Remove the behavior that retries a missing `contents.id` as `news_items.id`. The tables have
independent keyspaces. Canonical News routes return News DTOs and Content routes return Content
DTOs. The old ambiguous endpoint remains Python-owned only for a measured compatibility window if
installed-client telemetry proves it is still used. Rust must never implement the numeric fallback.
The intended canonical News detail identity is `/api/news/items/{news_item_id}`; a missing Content
detail returns 404 rather than probing that independent keyspace.

#### Repository-enforced quality gate

Add required CI before Rust deployment begins. Deployment consumes a tested SHA/artifact and has
an explicit dependency on the quality jobs. The transition matrix includes:

- Python focused/full tests while Python owners remain;
- Ruff, formatting, MyPy, architecture and module guards;
- public contracts plus Swift and Go generation checks;
- Go tests and vet;
- native iOS contract/unit gates appropriate to backend contract changes;
- Rust format, Clippy with warnings denied, workspace tests/nextest, dependency policy, SQLx query
  preparation, schema migration, and contract drift;
- container build and deployment manifest rendering.

Production deployment must never be the first workflow to discover that the tested source does
not build.

#### Repair typed-response coverage

Make the contract test inspect effective API operations rather than top-level FastAPI route
objects. Add a nonzero expected operation count, operation-ID comparison with checked-in OpenAPI,
and explicit exceptions for 204 and streaming responses. Validate declared success and typed error
responses. Contract freezing for Rust is blocked until this gate proves it sees the complete
surface.

### 8.2 Phase 0B: contract truth and telemetry

Create a baseline corpus before the first Rust handler:

- checked-in OpenAPI and per-operation request/response/error fixtures;
- absent versus `null` versus defaulted field matrices;
- exact UTC `Z` timestamp fixtures;
- representative legacy JSONB metadata;
- queue payload and transition fixtures;
- JWT, API-key, refresh replay, and account-deletion fixtures;
- Pydantic-AI history, chat progress, stream-generation, and provider-response fixtures;
- schema fingerprint at the chosen Alembic head;
- E2B command-stream, lifecycle, path, and error recordings;
- Crawl extraction golden cases;
- provider request/routing and usage fixtures.

Add a checked-in desired ownership manifest with route, task type, database writer, E2B namespace,
planned owner, cutover condition, rollback switch, and deletion condition. It documents and tests
the plan; the versioned runtime ownership registry defined below is the sole live source of truth.

Add privacy-safe endpoint/caller telemetry before removing duplicate routes. Track client version,
operation ID, status class, and last-seen date, not content or credentials.

### 8.3 Phase 0C: safe subtraction

Delete only after a fresh symbol, string-dispatch, configuration, generated-contract, and runtime
registration search:

- verified unused metadata modules and response reexports;
- blanket `F401` import surfaces that are not public registries;
- zero-caller internal Swift methods and endpoint constants;
- test-only production helpers with no runtime registration;
- obsolete internal chat/discovery helpers after the canonical path is selected.

Current candidates to re-prove include the docstring-only
`app/models/metadata/discussion.py`, unused response reexports in
`app/routers/api_content.py`, file-wide `F401` suppression in API model modules, and zero-caller
client helpers such as `ContentService.getChatGPTUrl`,
`AudioEpisodeService.createFastNewsEpisode`, `ChatService.startAdHocChat`, the service-level
`startDeepResearch`, counterargument prompt helpers, unused image-cache methods, and unused
endpoint constants. Test-only helpers such as `PaginatedFeed.refreshInBackground` and the
no-argument `ChatDependencies.live()` receive the same fresh proof before deletion.

Do not delete public endpoints, serialized fields, or generated-contract entries on static search
alone. `/chat-url`, fast-news audio, narration, sharing, and other public routes require telemetry
and installed-client compatibility evidence. Fields under active visual work also remain until that
work is reconciled. In particular, `feedPreview`, `artifactType`, `previewBullets`, and
`reasonToRead` are not deletion candidates until the visual-refresh work has settled.

Remove obsolete concepts before splitting or translating large modules. The desired end state is
not a Rust version of the current 1,906-line chat agent, 1,503-line assistant router, or 1,711-line
HTML strategy with all legacy branches intact.

### 8.4 Contract redesign lane

Behavioral correction and language cutover should normally happen in different releases. The
following changes should land while Python remains available as a compatibility owner, then become
the Rust contract:

1. Make generated wire contracts the only network boundary, including extension-safe generated
   `ShareActionCreateRequest` and `ShareActionResponse`. Decode a typed response, or deliberately
   change an endpoint to 204; do not discard an undocumented body. Establish and burn down a
   reviewed allowlist for all 60 currently handwritten iOS DTO exceptions.
2. Introduce a universal error envelope with `code`, `message`, typed/structured `details`,
   `retryable`, and `request_id`. Apply it to validation and documented non-2xx responses. Remove
   Swift authentication decisions based on English message substrings, and ensure OpenAPI includes
   real success statuses such as Briefing 304 and scraper-subscription 200.
3. Introduce owned `NewsItemSummary`, `NewsItemDetail`, and `NewsItemList` contracts. Retire the
   45-field, heavily nullable Content/News mixture, the Content-to-News adapter, and numeric-ID
   fallback through telemetry-backed compatibility.
4. Record field-presence policy separately from server construction defaults. A default used to
   construct a response does not imply that old/missing wire data is valid. Rust uses an explicit
   `MaybeUnset<T>` where omission and `null` differ. Inventory and resolve all 43 currently
   identified defaulted nonnullable fields whose Pydantic/OpenAPI requiredness, strict Swift
   decoding, and optional Go representation disagree.
5. Make pagination honest. Compute a real total when promised, otherwise omit/null it. Current-page
   length must not be presented as total result count. The six current producers for content lists,
   content search, Knowledge, recently read, chat sessions, and submission status are explicit
   migration fixtures.
6. Convert server-owned timestamp strings and empty-string sentinels to typed UTC timestamps.
   `SubmissionStatusResponse.created_at` and `processed_at` are initial examples.
7. Replace the large optional-field submission-status translator with a discriminated union for
   content, feed subscription, Learning Deck, and no-action outcomes.
8. Consolidate onboarding audio/fast discovery so completion sends a server-owned run identifier
   and selected suggestion identifiers rather than triggering duplicate discovery.
9. Retire duplicate route surfaces after telemetry: scraper aliases, chat session list aliases,
   narration aliases, old onboarding routes, and offset-pagination compatibility endpoints.
10. Retire `LearningDeckRun` only after `llm_tasks` backfill, compatible reads, production count
    proof, and a rollback window. Do not translate two canonical attempt ledgers into Rust.

Client structural cleanup runs alongside this lane. Finish one authenticated composition root and
consolidate iOS search cancellation before changing those APIs, but do not block the initial Rust
foundation on client-only refactoring.

## 9. Rust Workspace and Dependency Boundaries

```text
rust/
  Cargo.toml
  Cargo.lock
  crates/
    newsly-contracts        wire DTOs, schema generation, error envelope
    newsly-domain           IDs, state transitions, pure policies
    newsly-db               SQLx repositories, embedded migrations, backfills
    newsly-queue            task specs, lease protocol, worker kernel
    newsly-providers        model, media, search, image, storage gateways
    newsly-agent-runtime    Newsly transcript, tool loop, limits, usage
    newsly-e2b              direct control/envd/files/network client
    newsly-extraction       typed Python extractor client
    newsly-api              Axum routes and middleware
    newsly-worker           task executors and scheduler entrypoints
    newsly-admin            operator/admin commands and web presentation
    newsly-eval-driver      production-algorithm eval CLI
    newsly-vm-bootstrap     static helper installed in the E2B template
```

Dependency direction:

```text
API / worker / admin executables
          |
commands and queries / task executors
          |
domain policies + gateway traits
          |
SQLx repositories / provider adapters / E2B / extraction client
```

Provider, E2B transport, and extraction crates do not import `newsly-db`. E2B lifecycle
orchestration composes the transport with a lifecycle repository owned by `newsly-db`.
Repositories do not call external providers. Axum handlers own no SQL beyond transaction-free
orchestration.

Recommended base stack:

- Axum, Tokio, Tower, `tower-http`;
- SQLx for PostgreSQL SQL, listeners, transactions, and migrations;
- Serde for wire/storage representation;
- Schemars for tool and LLM JSON Schema;
- Utoipa for OpenAPI 3.1;
- Garde plus explicit `TryFrom` for boundary/domain validation;
- Reqwest with rustls for HTTP;
- `tracing` and OpenTelemetry for observability;
- `thiserror` for typed library errors and `anyhow` only at executable boundaries;
- `secrecy` for credential-bearing values;
- Minijinja for retained server-rendered admin pages;
- Clap for operator and migration binaries.

Pin exact dependency versions for provider/agent/E2B protocol crates and commit `Cargo.lock`.

## 10. Contract and Type Migration

### 10.1 Contract corpus

Create a language-neutral corpus:

```text
contracts/
  openapi/
  tasks/
  metadata/
  llm/
  extraction/
  evals/
  fixtures/
  policy-manifest.toml
```

During coexistence, current Pydantic models remain authoritative for endpoints still owned by
Python. Each migrated Rust route must emit an equivalent OpenAPI fragment and pass the same golden
fixtures before ownership moves. The combined public schema is generated from the ownership
manifest rather than allowing either runtime to claim the whole document prematurely.

After all public routes move, Rust becomes authoritative. Remaining Python extraction/eval models
are generated from or checked against the language-neutral schemas.

### 10.2 Rust representation rules

- Derive `Serialize`, `Deserialize`, `JsonSchema`, and `ToSchema` only where each representation is
  actually part of the type's role.
- Keep wire DTOs distinct from domain types and database rows.
- Use newtypes for `UserId`, `ContentId`, `NewsItemId`, `ChatSessionId`, `LlmTaskId`, `LeaseToken`,
  `StreamGeneration`, and `BriefingVersion`.
- Use `TryFrom<WireType>` for validation and normalization.
- Preserve aliases, input-only/output-only fields, tagged unions, custom serialization, defaults,
  decimals, URLs, UUIDs, and exact datetime behavior.
- Model open enums with `Unknown(String)`; reject unknown closed enums.
- Preserve explicitly lenient JSONB extensions instead of making Rust accidentally stricter.
- Use `MaybeUnset<T>` or an equivalent three-state representation when absent, `null`, and value
  differ.
- Snapshot every provider-facing JSON Schema because generation shape can change without a domain
  semantic change.
- Use code generation only for wire declarations and fixtures, never for commands, state
  transitions, or business policies.

## 11. SQLx Query and Migration Ownership

SQLx is selected over an ORM or a split SQL/migration stack because Newsly depends on explicit
PostgreSQL behavior: `FOR UPDATE SKIP LOCKED`, advisory locks, JSONB, GIN/trigram/FTS, partial
indexes, `ON CONFLICT`, transaction timestamps, and `LISTEN/NOTIFY`.

### 11.1 Query policy

- Prefer checked `query_as!` and `query_file_as!` statements.
- Use `QueryBuilder<Postgres>` only for genuinely dynamic filters. SQL fragments are allow-listed;
  all values use bind parameters.
- Keep complex queue/search SQL explicit rather than hiding it behind an ORM DSL.
- Commit `.sqlx` offline metadata.
- CI runs online `cargo sqlx prepare --check` against a real migrated PostgreSQL database with all
  required extensions, plus an offline workspace build.
- Repository methods return owned database projections or domain inputs, never a live row wrapper
  that can lazily query later.

### 11.2 Alembic-to-SQLx adoption

Migration ownership moves early, before broad route migration:

1. At implementation time, freeze Alembic at the then-current verified single head. The current
   candidate is `20260829_02`, but the adoption command must not assume this if the head changes
   before work begins.
2. Build a fresh PostgreSQL database through that head. Dump and normalize its schema, then audit
   extensions, functions, triggers, indexes, constraints, sequences, and required seed data.
3. Store that complete schema as the first SQLx baseline migration. Do not translate the historical
   Alembic revisions one by one.
4. Fresh databases execute the baseline normally.
5. Existing databases run `newsly-db baseline`. It verifies the expected Alembic head and a
   canonical schema and data fingerprint, then invokes the pinned SQLx 0.9
   `EMBEDDED_MIGRATOR.skip(&mut connection, Some(BASELINE_VERSION))` API. That API records every
   embedded migration through the baseline version with its exact SQLx checksum without executing
   its SQL. The adoption command is the only permitted caller. An empty history or an exact
   checksum-matching prefix produced from the same embedded baseline is resumable; gaps, checksum
   mismatches, down/newer rows, and unrelated migration history are rejected.
6. The fingerprint is a normalized catalog inventory, not a raw `pg_dump` hash. It includes
   extension names/versions; schemas; columns, types, nullability, defaults, identity and sequence
   ownership; constraints and validity; index definitions plus `indisvalid`/`indisready`;
   functions/procedures; triggers; row-level policies; required grants; and required seed/data
   invariants. It excludes physical OIDs, statistics, storage locations, and environment-specific
   owner role names while checking those roles/grants through a separate policy manifest.
7. Run fresh, adopted, exact-prefix resume, invalid-partial-history, Alembic-head mismatch, schema
   mismatch, required-data mismatch, interrupted adoption, and idempotent rerun tests before
   production.
8. Perform the one-time adoption in a brief maintenance barrier: stop/drain all application and
   migration writers, acquire one Newsly adoption advisory lock on a dedicated SQLx connection,
   verify the Alembic head plus schema/data fingerprint, execute `skip`, run the first pending SQLx
   migrations, and verify the final catalog before releasing the same connection/lock. This closes
   the verification-to-adoption race; Alembic is not assumed to honor the SQLx migration lock.
9. Update the Python-era Compose and blue/green deployment entrypoints to invoke the exact-SHA
   `newsly-db` binary instead of Alembic, then deploy adoption once. Freeze
   `migrations/alembic/` as immutable history. Every subsequent schema change is SQLx-owned,
   including those needed by still-running Python slices.

There is never a period in which both Alembic and SQLx accept new migrations.

### 11.3 Migration execution and policy

- Embed migrations in the exact-SHA `newsly-db` binary with `sqlx::migrate!`.
- A single pre-deploy migration job runs the binary. API and worker replicas never auto-migrate.
- Use timestamped reversible migration pairs by default and test up/down/up where reversal is
  promised.
- Never edit an applied migration; SQLx checksums are authoritative. Force LF endings for SQL.
- Treat production rollback as code rollback against forward-compatible expanded schema or as a
  roll-forward repair. Down migrations are not a substitute for restoring lost data.
- Keep small bounded DML inside transactional migrations.
- Run large transformations through idempotent, resumable `newsly-db backfill <name>` jobs with
  chunk checkpoints, metrics, validation, and a final constraint/contract migration.
- Put nontransactional PostgreSQL DDL such as `CREATE INDEX CONCURRENTLY` in a dedicated
  `-- no-transaction` migration, one operation per file.
- Use expand -> compatible code/backfill -> validation -> contract releases. Contract steps occur
  only after the old Python/Rust owner and rollback slot no longer read the old shape.

## 12. Transaction, Queue, and Ownership Model

### 12.1 Canonical runtime ownership registry

Use one PostgreSQL registry for live runtime ownership:

```text
runtime_ownership
  resource_kind       route_group | task_type | vm_namespace | state_writer
  resource_key
  active_owner        python | rust
  active_version
  desired_owner       python | rust | null
  desired_version     null when no transition is prepared
  transition_state    active | preparing
  updated_at
  updated_by
  reason

runtime_ownership_ack
  resource_kind, resource_key, desired_version, replica_id
  readiness_state     loaded | write_barrier | ready
  acknowledged_at

runtime_ownership_audit
  old/new owner and version, exact application SHA, actor, reason, timestamps
```

The exact-SHA `newsly-admin ownership transition` command is the only mutator of owner/version
transitions. Normal account/namespace lifecycle may create or tombstone registry resources through
one repository, but cannot change an established owner. The command requires expected versions,
validates resource-specific drain/readiness conditions, and supports atomic batches when one
product slice spans route, task, and namespace resources.

Promotion is durable and two-phase:

1. compare-and-set `desired_owner`/`desired_version` while leaving `active_owner` unchanged;
2. prove the target is ready and the source has reached its resource-specific drain point;
3. require every healthy gateway replica to load the desired version, enter a bounded write
   barrier, drain in-flight writes, and persist an acknowledgement;
4. promote desired to active in one audited compare-and-set transaction;
5. have replicas resume writes only after observing the active version;
6. clear acknowledgements after the rollback window.

Every owner-side mutation receives and validates the internal ownership version. A stale gateway
or restarted old replica may serve a read, but its stale write is rejected before mutation. Python
enqueuers and the Rust gateway consume the same registry. The gateway retains the last verified
active read owner if refresh fails and fails closed for writes whose active version it cannot prove.
There is therefore one effective write destination even during replica propagation.

The checked-in ownership manifest is a desired-state/deletion plan and CI fixture, not a second
runtime control plane. Per-task `executor_runtime` and per-namespace `ownership_version` are durable
stamps proving which registry decision created a claim; they cannot be mutated to simulate a flip.

### 12.2 Route ownership

The gateway routes complete endpoint groups to either Python or Rust. A read may be shadowed to the
non-owner and compared after redaction. A write is sent to exactly one owner. Contract corrections
normally land before or after language cutover, not in the same release.

### 12.3 Task ownership

Add explicit runtime ownership to durable work before Rust workers claim production tasks:

```text
processing_tasks.executor_runtime = python | rust
```

Enqueue stamps the current owner and version from `runtime_ownership`. Claim queries
filter by owner. Prefer partition cutovers; use task-type cutover where partitions mix materially
different risk. Existing Python-stamped work drains under Python while newly stamped Rust work is
canaried. Rollback flips the enqueue owner back without allowing Rust to finalize Python claims.

### 12.4 Rust queue kernel

Port and test the queue before porting complex handlers:

- claim with `SKIP LOCKED` and a new opaque lease token;
- exact owner/token/retry-generation/unexpired-lease renewal and finalization;
- success, retry, defer-without-retry, terminal failure, and cancellation;
- lease expiry/reclaim and stale-worker rejection;
- PostgreSQL notifications plus polling fallback;
- bounded heartbeat using independent short SQLx transactions;
- queue metrics, watchdog recovery, and administrator actions through the same canonical
  transition functions.

Handlers receive an immutable task envelope and a claim-bound lease/finalization capability, not a
general database transaction.

### 12.5 Stateful ownership beyond tasks

Add durable owner/version fields where two runtimes could otherwise act on the same state:

- chat session runtime and transcript version;
- E2B namespace runtime and lease fence;
- runtime ownership versions and checked-in desired/deletion manifests;
- versioned provider response and artifact attempt records where needed.

## 13. Agent and Provider Runtime

There is no official Rust SDK across every required provider. Use a Newsly-owned abstraction so
framework choice remains reversible.

### 13.1 Candidate stack

- Rig is the initial agent-loop candidate for multi-provider tools, streaming, structured output,
  dynamic tool filtering, and OpenRouter routing.
- `async-openai` is used behind `OpenAiGateway` for background Responses create/retrieve/cancel,
  embeddings, files, and provider-native features Rig does not expose precisely.
- Direct Reqwest/Serde adapters remain the escape hatch for new or vendor-specific fields.
- `rust-genai` is the fallback if the team chooses to own the complete agent loop after the canary.

Rig and `async-openai` are execution adapters. Newsly owns:

- `NewslyTranscript`, tool calls/results, usage, response IDs, and errors;
- tool-loop, request, token, deadline, and cancellation limits;
- dynamic tool selection and host/VM authority;
- structured-output validation and bounded repair;
- provider route/privacy settings and BYO-key handling;
- progress versus confirmed-final-text semantics;
- retry classification, persistence, and cost accounting.

### 13.2 Required bake-off

The decision to retain Rig is gated by production-shaped canaries:

1. OpenAI Responses multi-tool streaming with confirmed final text.
2. OpenAI background Deep Research create, retrieve, stream/retrieve, cancel, and resume.
3. Anthropic tool use and cache-control behavior.
4. Gemini structured output and thinking settings.
5. Exact OpenRouter provider pinning, fallback disabled, required parameters, data collection
   denied, ZDR, and reasoning policy.
6. Native, prompted, text, and tool-based structured-output strategies.
7. Malformed output followed by bounded validation repair.
8. Sequential/parallel complex tool arguments.
9. Usage, cached/reasoning token, request/response ID, timeout, cancellation, and retry reporting.
10. Replay of converted legacy Pydantic-AI histories and SDK upgrade fixtures.

Judge completed validated-object latency, schema correctness, and tool correctness rather than
time-to-first-token alone.

### 13.3 Persisted chat history

Pydantic-AI message JSON is a durable database contract today. Introduce a provider-neutral,
versioned `NewslyTranscript` before moving chat:

1. Freeze representative legacy fixtures and build a lossless Rust decoder.
2. Add `runtime_owner` and `transcript_version` to sessions.
3. Keep existing Python-owned sessions on Python initially.
4. Route new canary sessions to Rust and write only the Newsly format.
5. Project both formats to the unchanged public chat DTO.
6. Keep tool progress and partial text separate from the transcript and fenced by
   `stream_generation`/revision.
7. Backfill only after replay, round-trip, tool-order, and provider-resume tests pass.
8. Remove the legacy decoder after production count reaches zero and the rollback window closes.

## 14. Direct Rust E2B Integration

The final runtime calls E2B directly. Current E2B command streaming is a ConnectRPC server stream
over HTTP/2 using `application/connect+json`, not SSE or WebSocket. Use E2B's documented control
and envd APIs rather than retaining a Python SDK gateway.

### 14.1 `newsly-e2b` structure

```text
newsly-e2b/
  control_plane.rs   create/connect/resume/kill/snapshot
  envd_process.rs    start/connect/list/signal/stdin
  files.rs           bounded streaming upload/download
  network.rs         deny/allow policy mutation and reset
  lifecycle.rs       pooling, lazy acquisition, revision invalidation
  session.rs         Newsly-owned SandboxProvider implementation
  error.rs           stable retry/terminal/ambiguous classifications
  generated/         pinned minimal vendor protocol descriptors
```

Use Reqwest for documented REST/control/filesystem calls. Use a pinned ConnectRPC Rust client for
envd process streaming. E2B does not currently publish a first-party Rust SDK. Vendor only the
minimal authoritative protocol files from the E2B infrastructure source, currently
`e2b-dev/infra/packages/envd/spec/process/process.proto` and the required filesystem definitions,
at the commit pinned by `e2b-dev/E2B/spec/infra-ref`. Record repository, path, commit, and checksum;
generate Rust bindings with one exactly pinned pre-1.0 ConnectRPC client; and compare the used API
paths/descriptors against upstream in CI. Do not auto-upgrade them.

The client records and checks the envd version/capabilities before using a feature. Control-plane
requests and sandbox routing must implement the documented API key/access-token flow and headers,
including sandbox identity, envd port 49983, and secure-sandbox access token, without logging them.
Phase 1 must prove create, connect/resume, pause, kill, snapshots, network updates, file transfer,
streaming, timeout, quota, and missing-resource behavior against the actual production E2B
account. SDK/spec availability is not proof of account entitlement.

The public crate interface is Newsly-owned:

```text
SandboxProvider
  create / connect / kill
  create_snapshot / delete_snapshot
  update_network
  run -> CommandEventStream
  upload / download
```

### 14.2 Streaming semantics

Normalize envd events to:

```text
Started { sandbox_id, execution_id, pid }
Stdout { bytes }
Stderr { bytes }
KeepAlive
Exited { status, exit_code, error }
```

Requirements:

- separate incremental UTF-8 decoding for stdout and stderr;
- preserve observed event ordering while accumulating bounded channel-specific output;
- enforce byte limits during streaming, not after buffering;
- persist bounded tool progress with the active chat generation fence;
- keep keepalive events out of transcript/progress text;
- accept the currently documented status form and legacy exit-code form during transition;
- represent a nonzero exit as a completed structured command result;
- propagate cancellation through the HTTP/2 stream and explicit process signaling where needed;
- never create a snapshot while a Newsly command lease is active because snapshots break active
  streams/connections;
- use bounded channels/backpressure, absolute deadlines, idle deadlines, and explicit stream drain
  during shutdown.

Never blindly retry after request delivery may have occurred. Every command gets a unique Newsly
execution tag. On ambiguous transport failure:

1. health-check the sandbox;
2. find the process by execution tag/PID;
3. reconnect to the existing process if it is running;
4. read the terminal result manifest when available;
5. never automatically issue `Start` again.

For recoverable long jobs, `newsly-vm-bootstrap` tees bounded output to workspace files and writes
an atomic result manifest. The live stream is a progress channel; the manifest is the recovery
source of truth because output emitted before E2B reconnection is not replayed.

### 14.3 Full E2B ownership

Rust replaces more than `commands.run`:

- lazy sandbox create/connect/replace and process-local handle pooling;
- SQLx row/advisory locking and durable IDs through the E2B lifecycle repository;
- template revision invalidation;
- root hardening and capability probes;
- clean recovery snapshots and reconnect;
- full/delta corpus hydration with manifest-last installation;
- path normalization, file streaming, checksums, and size limits;
- command streaming and progress publication;
- feed-research network allow/reset and distributed serialization;
- account-deletion cleanup, diagnostics, stale-resource cleanup, and usage records;
- template build/check administration.

Replace embedded Python VM administration scripts with a static Rust helper in the E2B template:

```text
newsly-vm-bootstrap corpus install <archive>
newsly-vm-bootstrap feed fetch-batch
newsly-vm-bootstrap capabilities
newsly-vm-bootstrap command wrap <execution-id> -- <command>
```

Chromium, Playwright, Python, and Node may remain tools inside the isolated sandbox; they have no
Newsly host credentials or durable authority.

### 14.4 Namespace cutover

Python and Rust must never use the same persistent namespace concurrently. Add durable fencing:

```text
agent_vm_namespaces
  namespace
  ownership_version   references the canonical vm_namespace ownership decision
  lease_token
  lease_expires_at
  template_revision
```

Transfer all VM-backed features for a user namespace together. Transfer the singleton `user:0`
feed-research sandbox separately after its Python work drains. Rust may reconnect to an existing
sandbox ID after the owner flip. A snapshot ID is not connectable: when recovery is needed, Rust
creates a new sandbox from the snapshot under the namespace fence, applies the corpus delta, then
atomically replaces the durable sandbox ID. Failed creation/hardening/hydration is cleaned up
without publishing the replacement; the previous recoverable IDs remain until confirmed obsolete.

E2B migration order:

1. disposable live protocol spike;
2. Rust session/errors/deadlines/path/output layer;
3. security, corpus, template, snapshot, and static helper;
4. whole-namespace synthetic/dedicated-user canaries, executing Learning Deck, Share Action, and
   chat scenarios in that order while every VM-backed feature for those users is already routed to
   Rust;
5. whole-namespace selected-user chat/assistant expansion;
6. atomic `user:0` feed sandbox transfer;
7. template administration, account deletion, diagnostics, and Python dependency removal.

Protocol gates cover split UTF-8, interleaved stdout/stderr, keepalive, nonzero exit, output limits,
timeouts, cancellation, transport loss before/after delivery, reconnect without duplicate start,
sandbox kill, snapshot disconnect, file limits, and network reset on every exit path. Lifecycle
gates cover concurrent acquisition, stale templates, clean snapshot/delta recovery, corrupt remote
revision rebuild, sudo revocation, failed-hardening nonpublication, shutdown, and idempotent account
deletion.

## 15. Python Document Extractor

Keep the full extraction policy with Crawl4AI initially, not only the browser wrapper. The current
HTML strategy also contains static trafilatura/readability selection, source policies, access-gate
detection, cleanup, GitHub handling, Firecrawl fallback, tables, and feed-link discovery. Retaining
only raw Crawl4AI would change behavior and increase Chromium use.

Run one versioned private HTTP or Unix-socket service with a warm browser:

```text
ExtractRequest
  schema_version
  request_id
  url
  intent = static_analyze | extract_article | resolve_pubmed
  absolute_deadline
  bounded options
  trace identifiers

ExtractResult
  success { final_url, title, author, published_at, markdown,
            tables, feed_links, method, warnings, usage_events, timings }
  delegation { next_url }
  fallback_required { kind = firecrawl, url, reason }
  failure { code, retryable, http_status, bounded_message }
```

The extractor owns its profiles and refuses arbitrary Crawl4AI configuration from Rust. It has no
PostgreSQL, queue, Newsly token, user credential, persistence, retry, or downstream-enqueue access.
Rust validates the initial public URL and persists state/usage. The extractor independently
revalidates DNS and every redirect and runs under network-level egress restrictions.

Firecrawl is a Rust-owned external-provider adapter. The Python extractor owns the decision that
its local methods are exhausted and returns a typed `fallback_required` result, but it receives no
Firecrawl credential and writes no usage record. Rust performs the fallback call, persists usage,
and applies the typed result. Golden comparisons must cover this ownership split before removing
the old in-process fallback.

Shadow extraction compares typed results without writing state twice. Rust owns `ANALYZE_URL` and
`PROCESS_CONTENT` only after static fast-path, browser fallback, access gates, PubMed delegation,
HN linked content, timeouts, recycle behavior, and feed-link golden cases pass.

## 16. Python Eval and Embedding Boundary

Create a standalone `python/evals` environment. It owns:

- datasets and case construction;
- candidate model selection and local SentenceTransformers/Torch inference;
- provider/judge orchestration for experiments;
- call/cost controls, aggregation, reports, and visualization.

Rust owns production behavior:

- canonical matching text and stable hashes;
- exact/lexical candidate retrieval;
- semantic prefiltering, scoring, thresholds, guards, and reranking decisions;
- cluster reconciliation and decision traces;
- hosted production embedding/provider calls;
- Briefing centroid normalization, dimensions, model identity, update, and reset semantics.

Replace Python monkeypatch/ORM coupling with a versioned protocol:

1. `newsly eval prepare-relations` reads language-neutral cases and emits every canonical text/hash
   the matcher can request.
2. Python encodes those texts and writes an embedding bundle containing model identity,
   dimensions, normalization, hashes, vectors, timings, and provider metadata.
3. `newsly eval score-relations` consumes cases and bundle and executes the actual Rust matcher.

Support `matcher` mode for pure/in-memory threshold sweeps and `pipeline` mode for real SQLx
candidate retrieval inside a rolled-back test transaction. Exploratory direct Python provider calls
remain useful but cannot count as production parity or a promotion gate.

## 17. Other Python Runtime Debt

The strict end state has no unnamed Python backend islands.

- Hosted transcription is called from Rust. If local transcription remains a requirement, prove a
  `whisper-rs`/equivalent path against hardware, accuracy, and latency fixtures; otherwise remove
  local `faster-whisper` from production.
- `yt-dlp` may be a temporary supervised executable while source-specific Rust/provider paths are
  built, but it needs a named owner, telemetry, supported-source scope, and removal/acceptance
  decision. Rust owns process limits, output parsing, retries, and state throughout.
- Retired Python contract generators are migration evidence only. The Rust Utoipa export and
  schema-native tooling own the contract corpus; no Python generator is a runtime or CI authority.
- Existing Python E2B SDKs and Alembic are removed after their explicit adoption/cutover gates.

## 18. Migration Program

Durations are rough engineering estimates, not delivery commitments. Phases overlap only where
their ownership and schema dependencies allow it.

### Phase 0 — stabilize, simplify, and establish truth (4-8 weeks)

Owner: current Python/client system.

- Fix the four correctness/release blockers.
- Establish required CI and tested-SHA deployment.
- Build the golden contract/error/presence/queue/E2B corpus and ownership manifest.
- Add endpoint/client-version telemetry.
- Perform verified safe subtraction.
- Land high-value contract corrections in independently compatible slices.

Exit: contract tests cover a nonzero known operation set; long external-call tests prove no open
transaction; Content/News identity is unambiguous; deployment depends on green quality jobs. After
the concurrent visual-refresh work settles, run the complete release gate on one clean SHA,
including every outstanding Python and iOS visual-related assertion and the updated
personalized-onboarding intro/voice flow. Historical mixed-checkout counts do not satisfy this exit
gate.

Rollback: ordinary current-system code rollback; no Rust production ownership yet.

### Phase 1 — Rust foundation, SQLx adoption, SDK and E2B spikes (6-10 weeks)

Owner: infrastructure/contracts.

- Create the Rust workspace, build conventions, observability, config/secrets, and health binary.
- Implement contract schema/fixture comparison.
- Establish SQLx baseline, fresh/adoption tests, exact-SHA migration binary, and retire active
  Alembic ownership.
- Implement JWT/API-key read validation and safe HTTP foundations.
- Run the Rig/provider bake-off.
- Run the disposable direct-E2B protocol spike, including ConnectRPC streaming and recovery.

Exit: a deployable Rust service and `newsly-db` binary exist; SQLx owns all new migrations; one
authenticated read route has exact fixture/OpenAPI parity; agent/E2B go/no-go evidence is recorded.

Rollback: route remains Python; SQLx-expanded schema remains compatible; migration adoption is
roll-forward and does not require Alembic reactivation.

### Phase 2 — read APIs and corrected contracts (4-8 weeks)

Owner: Rust API for selected groups.

- Move health/status, jobs/statistics, then content/news/Knowledge reads and search.
- Introduce the typed error envelope and corrected presence/pagination/time types in planned
  client-compatible releases.
- Shadow and compare read results, authorization, query plans, and latency.

Exit: selected route groups are Rust-owned for a production soak; public contract and generated
clients remain exact; fallback to Python is tested.

### Phase 3 — simple writes and queue kernel (8-12 weeks)

Owner: Rust API/queue for selected groups.

- Move feedback, analytics, read/Knowledge state, API keys, and narrow idempotent commands.
- Port the full SQLx lease/notification/finalization kernel.
- Add runtime ownership stamping and transfer low-risk task families/partitions.
- Enforce prepare/external/finalize transactions for every new executor.

Exit: no duplicate writes/claims; concurrency/fault suites pass; Rust tasks complete a production
soak with tested owner flip-back.

### Phase 4 — ingestion and Crawl4AI boundary (8-12 weeks)

Owner: Rust content orchestration plus Python extraction only.

- Split and deploy the DB-less document extractor.
- Port feed/source HTTP, ingestion, analysis orchestration, persistence, and downstream enqueueing.
- Preserve static extraction bypass, Crawl fallback policy, URL safety, and usage accounting.

Exit: content task families are Rust-owned; extraction golden and production shadow comparisons
pass; the extractor image contains no database/application backend.

### Phase 5 — bounded LLM, embeddings, media, and integrations (8-14 weeks)

Owner: Rust providers/workers.

- Move summaries, classification, onboarding, discovery, news relations, Briefing embedding state,
  image/audio/provider adapters, storage, and external integrations.
- Convert Python evals to the Rust eval protocol.
- Resolve local Whisper and `yt-dlp` transitional debt.
- Consolidate duplicate onboarding and submission workflows before porting them.

Exit: structured outputs, provider routing/privacy, usage/cost, retry, and eval promotion gates
match production contracts.

### Phase 6 — E2B, agents, Briefing, and chat (14-24 weeks)

Owner: Rust agent/E2B runtime.

- Complete Rust E2B lifecycle, helper binary, corpus, files, snapshots, and streaming.
- Transfer whole dedicated/synthetic user namespaces to Rust and exercise Learning Deck, Share
  Action, then chat/assistant scenarios without returning any feature in that namespace to Python;
  expand by whole selected-user namespaces, then transfer the separate system feed namespace.
- Introduce and backfill `NewslyTranscript`.
- Port Deep Research resume, dynamic tools, artifact validation/publication, and Briefing
  composition/publication.
- Retire `LearningDeckRun` only after canonical `llm_tasks` proof.

Exit: all E2B namespaces and agent sessions are Rust-owned; no Python E2B packages remain;
stream/fence/reconnect/ambiguity canaries and product validators pass.

### Phase 7 — auth, admin, operations, and Python backend retirement (8-12 weeks)

Owner: Rust production runtime.

- Move Apple sign-in/JWKS, refresh replay rotation, credential crypto, account deletion, scheduler,
  admin web/CLI, health/repair commands, retention, and cleanup.
- Remove remaining FastAPI/SQLAlchemy/Pydantic-AI application paths.
- Remove inactive compatibility routes and fields whose telemetry/deletion conditions are met.
- Split final images and operational runbooks.

Exit: Rust owns the full production backend and SQLx owns schema history after the baseline;
production Python is only the document extractor; offline Python is only eval/model tooling.

With two or three experienced engineers, the first production Rust slice is likely 8-12 weeks and
the full program roughly 10-16 months. Primarily solo execution is more likely 18-28 months. Direct
E2B protocol work, contract cleanup, agent SDK churn, and legacy data conversion are the largest
schedule multipliers.

## 19. Validation Matrix

Every slice has focused tests plus the following applicable gates.

### Contracts and clients

- complete effective-route coverage and known operation count;
- OpenAPI/JSON Schema diff and golden requests/responses/errors;
- open/closed enums, missing/null/default, UTC, aliases, and lenience;
- generated Swift/Go/Share Extension compile and fixture decoding;
- old-client compatibility for the supported version window.

### PostgreSQL and migrations

- real PostgreSQL with required extensions, never SQLite substitution;
- empty database -> SQLx baseline -> head;
- Alembic-head snapshot -> fingerprint -> SQLx baseline skip -> head;
- online SQLx query preparation plus offline build;
- up/down/up for promised reversible migrations;
- nontransactional DDL and concurrent application traffic;
- resumable backfill interruption/restart and final constraint validation;
- old Python/new Rust compatibility around every expand/contract step.

### Transactions and queue

- no transaction/pool hold across slow provider/E2B/image/storage calls;
- concurrent claim uniqueness and notification/poll fallback;
- lease renewal, expiry, reclaim, defer, retry, and terminal behavior;
- stale worker, same-worker-name/new-token, and generation-fence rejection;
- queue owner flip, in-flight drain, and rollback;
- Briefing publication and account-deletion atomicity.

### Agents, providers, and E2B

- real structured contracts across configured providers;
- tool schema, tool filtering, usage, cancellation, repair, and response resumption;
- converted transcript replay and confirmed-final-text behavior;
- cold/warm E2B acquisition, snapshot recovery, corpus deltas, security hardening;
- streaming chunk/order/UTF-8/output-bound/timeout/cancel/reconnect tests;
- no duplicate command after ambiguous delivery;
- Learning Deck, Share Action, chat, and feed-research live canaries;
- no leaked sandboxes, snapshots, or processes.

### Crawl and evals

- static/browser/fallback extraction goldens, public-address enforcement, and resource bounds;
- typed Python/Rust protocol conformance;
- embedding vector hash/dimension/normalization/model checks;
- pure matcher and real SQL candidate-retrieval eval modes;
- validated-object latency, schema correctness, quality, usage, and maximum-cost reporting.

### Production acceptance

- read canary for 3-7 days before broadening;
- write/worker/agent canary for 7-14 days where risk warrants it;
- p50/p95 latency, error, queue age, DB pool/transaction age, provider cost, E2B activation, and
  extraction comparison dashboards;
- explicit rollback rehearsal before owner expansion;
- exact tested SHA/image and live health proof.

## 20. Rollout and Rollback

- Route rollback changes gateway ownership; it never replays a write to both runtimes.
- Task rollback changes the enqueue owner. Existing tasks drain under the runtime stamped on them.
- E2B rollback changes a namespace owner only after active command leases drain. One namespace is
  never live in both runtimes.
- Database rollback normally deploys previous code against forward-compatible expanded schema.
  Destructive rollback uses verified backup/roll-forward repair, not an assumed down migration.
- Provider rollback changes the Newsly adapter/configuration, not durable transcript representation.
- Deletion follows successful soak plus zero-dependency proof. Compatibility code is not removed in
  the same release as first ownership cutover.

## 21. Principal Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Rust encodes current contract bugs as parity | Phase 0 blocker and redesign classification; do not compare against known-wrong behavior |
| SQLx baseline differs from production | exact Alembic-head and schema/data fingerprint gate; fresh/adoption rehearsal; no unverified skip |
| Python/Rust both claim work | durable executor owner stamped at enqueue and filtered at claim |
| Both runtimes use one E2B sandbox | durable namespace owner plus lease fence; user-level atomic transfer |
| E2B protocol/descriptors drift | minimal pinned vendor descriptors, provenance/checksum, upstream drift CI, live canaries |
| Ambiguous E2B stream failure duplicates commands | execution tags, process reconnect, result manifests, never retry uncertain start |
| Rig or community SDK churn | exact pins, Newsly-owned types/interfaces, upgrade fixtures, direct HTTP escape hatch |
| Pydantic-AI history cannot replay | versioned transcript, legacy decoder, owner/version routing, staged backfill |
| Contract cleanup expands scope indefinitely | separate compatibility ledger, telemetry windows, explicit deletion conditions |
| Long coexistence creates two systems | route/task/namespace ownership manifest, single migration owner, scheduled Python deletion milestones |
| Porting large modules reproduces accidental complexity | Phase 0 subtraction/consolidation before module translation |

## 22. Completion Criteria

The migration is complete only when:

1. Rust owns every public/admin API route, command/query, database repository, queue task, worker,
   scheduler, authentication path, agent, E2B namespace, migration, and operator action.
2. SQLx is the only active PostgreSQL query/migration stack, with the audited baseline and complete
   forward history.
3. FastAPI, SQLAlchemy, Pydantic-AI, Alembic, and E2B Python SDKs are absent from the general
   production runtime.
4. The only production Python process is the DB-less document extractor, with a versioned bounded
   contract and no durable authority.
5. Offline Python evals exercise Rust production algorithms through versioned protocols rather
   than importing or reimplementing backend logic.
6. Generated iOS, Share Extension, and Go contracts cover every network boundary, including typed
   errors and intentional no-body responses.
7. No external call can hold an application transaction, and DB pool/transaction-age production
   telemetry proves it.
8. Queue, Briefing, chat, refresh replay, account deletion, E2B, extraction, and provider invariants
   pass fault/concurrency/live canaries.
9. Legacy routes, Pydantic-AI histories, and `LearningDeckRun` have either reached their deletion
   condition or are recorded as explicit external compatibility with an owner and removal policy.
   Runtime ownership history is retained only where it provides a useful audit/fencing record.
10. Production rollback, health diagnosis, and exact-SHA release proof are at least as strong as
    the current system.

## 23. Implementation Record Handoff

[`20-implementation-plan.md`](20-implementation-plan.md) decomposes the migration into ordered
work packages. Each package records:

- owning developer/runtime;
- dependencies and touched surfaces;
- product law and contract impact;
- database/route/task/namespace ownership before and after;
- tests and production canary;
- rollback mechanism;
- compatibility and deletion condition;
- expected duration and whether it can run in parallel.

The program started with correctness and evidence work rather than Rust business handlers:
effective route-contract coverage, required tested-SHA CI, short external-call transactions,
separate Content/News identity, the ownership and fixture corpus, and the Rust/SQLx foundation.
Current completion and remaining release proof are summarized in
[`30-summary.md`](30-summary.md).

## References

- [Current architecture](../../architecture.md)
- [Typed contract policy](../typed-contracts-2026-06/20-contract-policy.md)
- [VM execution layer](../vm-execution-layer-2026-08/plan.md)
- [Processing task lease ownership](../2026-07-30-processing-task-lease-ownership-design.md)
- [SQLx](https://github.com/transact-rs/sqlx)
- [SQLx migration API](https://docs.rs/sqlx/0.9.0/sqlx/migrate/struct.Migrator.html)
- [SQLx CLI and offline metadata](https://github.com/transact-rs/sqlx/blob/main/sqlx-cli/README.md)
- [E2B public OpenAPI](https://docs.e2b.dev/openapi-public.yaml)
- [E2B command streaming](https://docs.e2b.dev/commands/streaming)
- [E2B process connection](https://docs.e2b.dev/api-reference/process/connect)
- [E2B snapshots](https://docs.e2b.dev/sandbox/snapshots)
- [E2B envd architecture and protocol paths](https://github.com/e2b-dev/infra/blob/main/docs/ARCHITECTURE.md)
- [E2B process protocol](https://github.com/e2b-dev/infra/blob/main/packages/envd/spec/process/process.proto)
- [ConnectRPC Rust](https://github.com/connectrpc/connect-rust)
- [Rig](https://github.com/0xPlaygrounds/rig)
- [OpenAI SDK libraries](https://developers.openai.com/api/docs/libraries)
