# Rust Backend Migration Implementation Plan

Date: 2026-08-31. Status: implemented and consolidated local gates pass;
external-service canaries and production release remain outstanding.

This plan turns the approved [migration design](10-design.md) into ordered,
end-to-end work packages. “Implemented locally” means the code exists in the
current uncommitted working tree and its recorded focused checks passed. It does
not mean the complete release gate, a tested-SHA push, SQLx production adoption,
deployment, or production authority change has occurred.

## 1. End state

The final system has one application authority:

```text
iOS / Share Extension / Rust CLI / admin
                    |
                    v
           Rust Axum + Tokio API
                    |
     +--------------+---------------+
     |              |               |
     v              v               v
PostgreSQL      Rust providers   direct Rust E2B
SQLx state      Rig/direct HTTP  HTTP + ConnectRPC
and queue
     |
     v
Rust workers and scheduler ---> Python document extractor
                                 DB-less Crawl4AI boundary

offline only: python/evals <--> newsly-eval-driver artifacts
```

Rust owns every route, state transition, query, migration, queue claim,
provider call, agent loop, E2B namespace, and operator action. Newsly-owned
Python remains only in `python/document_extractor` and `python/evals`. The
application image's pinned third-party `yt-dlp` executable includes its own
Python runtime, but it runs only as a Rust-controlled media subprocess and owns
no Newsly state or workflow.

## 2. Phase 0 prerequisites: repair truth before parity

These were release blockers, not behaviors to reproduce in Rust.

### WP0.1 — short transaction ownership

- **Problem:** workers could hold PostgreSQL transactions across long LLM,
  image, E2B, storage, or crawler calls.
- **Change:** every operation produces an owned immutable work plan in a short
  prepare transaction, releases the connection, performs external work, then
  finalizes through a fresh transaction that revalidates the route/lease,
  attempt generation, and product lifecycle.
- **Gate:** PostgreSQL concurrency tests must prove an intentionally blocked
  provider does not block unrelated writes, queue heartbeats, or concurrent
  schema work. Lease-loss tests must prove stale output cannot publish.
- **Status:** the Rust crate boundaries and task executors use this shape;
  consolidated PostgreSQL concurrency coverage remains outstanding.

### WP0.2 — Content and News identity

- **Problem:** a missing Content detail could fall through to an unrelated News
  row with the same integer ID.
- **Change:** Content and News have separate canonical routes, DTOs, and SQLx
  repositories. Missing Content returns 404 and never probes the News keyspace.
- **Gate:** collision fixtures with equal numeric IDs, authorization failures,
  and installed-client route coverage.
- **Status:** implemented in the Rust HTTP surface; broad client parity remains
  part of the final gate.

### WP0.3 — required tested-SHA quality gate

- **Problem:** production deployment did not depend on a repository-enforced
  test job.
- **Change:** `.github/workflows/quality-gate.yml` owns required Python-island,
  Rust, SQLx, contract, Rust CLI, and native-client evidence. The deploy workflow
  depends on that reusable job and consumes its exact `tested_sha`.
- **Gate:** render and exercise the workflow, reject a stale main SHA, build the
  same image that is deployed, and retain the tested SHA in runtime health.
- **Status:** workflow dependency and tested-SHA plumbing are implemented;
  the complete remote workflow has not yet run for this working tree.

### WP0.4 — non-vacuous typed response coverage

- **Problem:** the old FastAPI contract test could pass after seeing zero
  effective `/api/` operations.
- **Change:** Rust Utoipa is the sole route/schema inventory. Export fails when
  the expected operation set is empty or a required operation is missing, and
  typed success/error responses are checked against committed artifacts.
- **Gate:** deliberate route removal and untyped-response mutations must make
  the check fail; streaming and intentional 204 operations remain explicit.
- **Status:** Rust public/agent export, fail-closed Swift/Share generation,
  artifact drift checks, and native-client compilation are implemented and pass
  locally. The remote exact-SHA quality workflow has not run for this tree.

## 3. Contract redesign and safe subtraction

These changes simplify the product boundary before final authority. They are
not an excuse to preserve ambiguous compatibility in Rust.

### WP1.1 — one generated wire boundary

- Rust Serde/Utoipa contracts own public requests and responses.
- Share Extension request, response, auth refresh, and failure shapes are part
  of the same generated surface.
- All public failures use one typed error envelope with stable code, safe
  message, structured details, and request identity.
- Missing, explicit `null`, and defaulted values are modeled deliberately.
- Public timestamps are typed UTC RFC 3339 values serialized with `Z`.
- Pagination omits an unknown total or returns a count backed by a real count
  query; page length is never presented as the global total.

### WP1.2 — canonical product concepts

- Canonical short-form News and legacy/long-form Content remain distinct types
  and route families.
- Submission status now has one generated discriminated `SubmissionResult`
  boundary for content, feed subscription, Learning Deck, and no-action
  outcomes. Temporary top-level installed-client fields must mirror that
  canonical result exactly and remain only until operation/client-version
  telemetry satisfies the documented compatibility window.
- Onboarding discovery has one canonical orchestration path.
- `LearningDeckRun` is not yet retired. Its read-only compatibility and cleanup
  paths remain until production counts, legacy-row backfill verification, and
  an explicit schema migration prove no remaining dependency.

### WP1.3 — subtract before splitting

Safe deletion includes verified-unused discussion metadata, response reexports,
blanket `F401` compatibility imports, zero-caller Swift methods and endpoint
constants, and test-only production helpers. Every candidate needs a fresh
symbol, string-dispatch, registration, generated-contract, and configuration
search.

Public endpoints are different: add privacy-safe operation/client-version
telemetry, define a last-seen window and minimum installed-client version, then
remove the route in a separately releasable contract change. Static search is
not enough.

Before large-file decomposition, delete obsolete chat and discovery paths. On
iOS, keep one process composition root and one owner for search cancellation;
do not copy lifecycle, dependency construction, or cancellation registries
into each feature.

**Status:** complete locally. The iOS application now resolves live services at
one explicit, instance-bound composition graph, and `SearchViewModel` owns
cancel-and-replace search work and stale-result fencing. Five formerly oversized
Rust modules were decomposed into cohesive feature children below the repository
ceiling, and their five temporary module-size exemptions were removed.

## 4. Rust foundation

### WP2.1 — workspace and process conventions

Create the edition-2024 Rust workspace with pinned dependencies, warning-denied
Clippy, structured tracing, validated/redacted configuration, health/readiness,
graceful cancellation, and exact-build metadata.

Primary choices:

- Axum 0.8, Tokio 1.53, and Tower for HTTP/runtime;
- SQLx 0.9 for all PostgreSQL access and migrations;
- Serde, Schemars, Utoipa, and explicit validation for contracts;
- Rig 0.42 behind `newsly-agent-runtime`;
- `async-openai` or typed `reqwest` for provider-native APIs;
- ConnectRPC plus `reqwest` for direct E2B.

**Status:** implemented locally. The clean warning-denied workspace Clippy and
full locked, offline workspace test gates pass.

### WP2.2 — SQLx migration authority

1. Freeze Alembic at `20260829_02`, capture its audited catalog in the SQLx
   baseline, then remove the inactive source tree while preserving git history.
2. Store the audited catalog as the first embedded SQLx migration.
3. Fresh databases execute the baseline.
4. Existing databases adopt it only while all writers are stopped and drained.
5. Hold one advisory lock across Alembic-head verification, catalog/data/role
   fingerprinting, recognition of an empty or exact checksum-matching prefix,
   SQLx baseline recording, pending migrations, and final verification.
6. Reject gaps, mismatches, invalid objects, and unverified skip requests.
7. Run all later DDL and resumable backfills through exact-SHA Rust tools.

**Status:** baseline, adoption operator, deployment entrypoint, authority
migration, and frozen-history guard are implemented locally. A fresh database
migrates successfully, SQLx prepare metadata is current, and the Rust API starts
against a throwaway migrated database with healthy `/health`, `/health/live`,
and `/health/ready` responses. Existing-database adoption/interruption rehearsal
under a production-shaped maintenance barrier remains a release gate;
production has not been adopted.

### WP2.3 — language-neutral contract corpus

Freeze public OpenAPI, task JSON Schemas, queue transitions, wire-presence and
UTC fixtures, legacy JSONB, auth/replay, provider/transcript, E2B, extraction,
eval, and schema fingerprints. Rust tests consume the corpus directly.

**Status:** the corpus, Rust public authority, and schema-native Swift and Share
Extension generation are implemented locally. Checked artifacts and drift
protection pass, as do the Rust CLI and consolidated native-client test gates.

## 5. Durable ownership and queue

### WP3.1 — two-phase ownership registry

Use PostgreSQL active and desired owner/version state, per-replica
acknowledgements, audit rows, and owner-side version checks. A cutover is:

1. prepare desired owner/version without changing active ownership;
2. make the target ready and drain the source;
3. have every healthy gateway load the version, enter a write barrier, and
   acknowledge the exact application SHA;
4. promote through one compare-and-set transaction;
5. resume only after replicas observe the active version.

Durable tasks and E2B namespaces retain the exact owner/version that created
them. Rollback changes new work; it does not rewrite an in-flight attempt.

**Status:** registry, CLI, fences, policy manifest, staged seeds, and final Rust
authority migration are implemented locally. Production acknowledgements and
drain proof have not run.

### WP3.2 — queue kernel

Port claim uniqueness, `SKIP LOCKED`, lease token and retry generation,
heartbeat, defer-without-retry, expiry/reclaim, terminal classification,
LISTEN/NOTIFY with polling fallback, and exact-lease finalization before task
business logic.

**Status:** implemented locally and used by Rust worker binaries. The full
PostgreSQL contention, crash, reclaim, and rollback suite remains outstanding.

## 6. Product slices

Each slice moves its route group, task contracts, SQLx repositories, provider
adapters, state projection, and operator visibility together. Focused package
checks are recorded in `docs/log.md`, and the consolidated local workspace and
client gates pass. Production-shaped PostgreSQL adoption and live provider/E2B
canaries remain release work.

| Slice | Rust ownership implemented locally | Required final canary |
|---|---|---|
| Auth, profile, refresh replay, account deletion, API keys | HTTP, SQLx, crypto, deletion worker | Apple/JWT/API-key/replay/account-deletion parity |
| Content, Knowledge, feeds, search, submissions | Routes, queries, writes, content worker | ID collision, visibility, pagination, body/feed lifecycle |
| Short-form News and relations | Enrichment, summaries, hosted embeddings, clustering | summary reuse, relation races, embedding policy, fan-out |
| Briefing | HTTP, refresh/publication tasks, narration state | source publication atomicity, polling, audio manifests |
| Learning Deck and Share Actions | HTTP, LLM tasks, artifacts, share tokens | legacy backfill, artifact validation, extension flows |
| Chat and deep research | durable turn worker, tools, progress, provider resumption | history conversion, generation fences, final text, cancellation |
| Onboarding and discovery | routes, task execution, model/search/feed adapters | resumability, duplicate discovery, installed-client flow |
| Audio, podcast, tweet media | downloads, yt-dlp/ffmpeg boundary, transcription, narration | size/path bounds, cleanup, provider and media fallback |
| Images and discussions | provider work, staging/publication, refresh claims | summary fingerprint, lease loss, cache version, soft failure |
| X integration and scrapers | OAuth refresh, bookmarks, ledger, scheduled ingestion | pagination/checkpoints, billing dedupe, reauth, deletion |
| Agent Data and VM features | corpus, Learning Deck, Share Action, chat tool execution | corpus revisions, security, snapshots, no-tool laziness |
| Admin, scheduler, health, usage, repairs | Rust HTTP/CLI/scheduled work | JSON envelopes, guarded mutation, runtime diagnostics |

## 7. Direct E2B work package

Rust owns sandbox create/connect/kill, snapshots, network policy, files, process
start/connect/signal, session pooling, advisory locks, template revision,
hardening, corpus hydration, recovery, diagnostics, usage, and deletion.

The stream adapter must:

- preserve observed stdout/stderr event ordering while decoding each stream
  incrementally;
- handle split or invalid terminal UTF-8, keepalives, nonzero exits, output
  bounds, deadlines, cancellation, and file limits;
- tag commands with durable Newsly identity;
- distinguish failure before request delivery from ambiguous delivery;
- on ambiguity, inspect and reattach to the existing process, never issue a
  blind second start;
- reset candidate-scoped network policy on success, failure, timeout, and
  cancellation;
- refuse snapshot creation while a command lease is active.

`newsly-vm-bootstrap` replaces host-authored Python helper scripts inside the
sandbox. Chromium, Playwright, Python, or Node may remain workload capabilities
inside E2B; they are not backend authorities.

**Status:** the direct Rust client, session/corpus/security layers, helper, and
feature integration are implemented locally. Disposable live E2B protocol,
cold/warm, snapshot, network-reset, leak, and account-deletion canaries remain
required before release.

## 8. Python boundaries

### WP6.1 — document extractor

`python/document_extractor` owns Crawl4AI plus the surrounding static/browser
extraction policy. It accepts a versioned, authenticated, bounded request and
returns a discriminated extraction, delegation, Firecrawl-required, or failure
result. It has no database, queue, app JWT, Firecrawl credential, durable retry,
or state-persistence access. Rust owns all of those concerns.

Release gates include public-address validation at every redirect/browser
request, warm-browser recycling, deadline/cancellation, size bounds, access
gates, PubMed delegation, HN linked articles, Firecrawl recovery, and typed
Rust/Python golden parity.

### WP6.2 — eval pipelines

`python/evals` owns datasets, candidate embeddings, local SentenceTransformers,
judges, cost controls, reports, and visualization. It exchanges versioned
JSON/NDJSON embedding bundles and decision traces with `newsly-eval-driver`.
Canonical text, SQL candidate retrieval, matching, thresholds, reranking
decisions, and production hosted embeddings remain Rust-owned.

Release gates cover hashes, dimensions, normalization, model identity,
in-memory sweeps, real PostgreSQL retrieval, schema validity, validated-object
latency, quality, usage, and maximum cost.

## 9. Final authority cutover and retirement

The local authority migration sets every route, task, writer, and namespace to
Rust only after the corresponding code exists. Before production applies it:

1. settle the working tree and produce one candidate SHA;
2. run the complete quality gate on that exact SHA;
3. build immutable Rust and extractor images from it;
4. rehearse fresh and existing-database SQLx migration paths;
5. run PostgreSQL, provider, E2B, extractor, Swift, Share Extension, and Rust CLI
   canaries;
6. stop and drain all old writers;
7. adopt SQLx and apply the authority migration under the maintenance barrier;
8. start Rust API/workers/scheduler and the isolated extractor;
9. verify public HTTPS, readiness, queues, transaction age, costs, sandbox
   leaks, and client flows;
10. keep the previous exact image only as a bounded emergency rollback target
    while the schema remains expand/contract compatible.

The local tree already removes the general Python application, admin,
migrations, backend Python tests, old runtime entrypoints, and duplicate
application packages. The release gate must prove that subtraction before the
production rollout removes the old image and processes. Retain only the two
named Newsly Python packages, the pinned third-party `yt-dlp` executable/runtime,
and immutable contract/git evidence that still explains persisted data or
external compatibility. Maestro flows and baselines live with the iOS client
under `client/newsly/Maestro/`.

## 10. Remaining validation and deploy status

Completed locally:

- Rust workspace and authoritative public contract;
- SQLx baseline, forward migrations, and final authority migration;
- Rust route, query, worker, scheduler, provider, Rig, and direct E2B code;
- DB-free document extractor and offline eval package;
- removal of the general `app/`, `admin/`, `migrations/`, and backend `tests/`
  trees, with Maestro flows/baselines moved under `client/newsly/Maestro/`;
- warning-denied Clippy and all 288 PostgreSQL-enabled Rust workspace tests,
  plus locked offline compilation;
- fresh-database SQLx migration and prepare checks plus a healthy Rust API smoke
  against that throwaway schema;
- 39 Python-island tests (4 eval and 35 extractor) plus Ruff, formatting, MyPy,
  database-boundary, and package-build checks;
- public-contract drift, Rust CLI tests, and all 632 native tests (629 unit and 3
  UI) with zero failures or skips;
- local and production Compose configuration plus shell syntax checks;
- required quality/deploy workflow dependency.

Still required:

- existing-database SQLx adoption/interruption, PostgreSQL contention, lease,
  and cross-feature parity under a production-shaped maintenance barrier;
- live or disposable provider, E2B, extractor, media, and object-storage canaries;
- production-shaped Docker build, which was unavailable locally because the
  Docker daemon is not running, and an exact-SHA deployment rehearsal;
- commit and push, which were not requested by the documentation task;
- production SQLx adoption, authority promotion, deployment, and health proof;
- any Apple distribution work.

Until those gates pass, the implementation is a local migration candidate, not
a production cutover.
