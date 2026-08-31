# Rust Backend Migration Summary

Date: 2026-08-31. Status: backend and Rust CLI implementation are complete
locally. The post-cutover workspace, contract, architecture, native-client, and
local product E2E gates pass; production adoption and deployment remain
outstanding.

## Outcome

Newsly now has one intended production backend: Rust.

- Axum, Tokio, and Tower own HTTP and process orchestration.
- SQLx owns all PostgreSQL queries, repositories, queue operations, and schema
  migrations.
- PostgreSQL remains the durable state store and task queue.
- Rig runs behind a Newsly-owned agent interface; `async-openai` and typed
  direct HTTP cover provider-native APIs.
- Rust integrates with E2B directly over its control-plane HTTP API and envd
  ConnectRPC streams. There is no Python or Node E2B gateway.
- Rust Utoipa types are the public OpenAPI authority.
- The user-facing `newsbuddy` CLI is a Rust/Clap workspace binary that consumes
  shared `newsly-contracts` request types. It preserves the existing binary,
  config paths and aliases, commands, output envelopes, QR login, and safe
  Markdown-library synchronization behavior.
- Newsly-owned Python remains only in `python/document_extractor`, the
  database-free Crawl4AI service, and `python/evals`, the offline
  model/evaluation package. The Rust application image also installs the pinned
  third-party `yt-dlp` executable and its Python runtime as a bounded media
  subprocess; it owns no Newsly application state or workflow.

The general `app/`, `admin/`, `migrations/`, and backend `tests/` trees have
been removed. Maestro flows and baselines now live under
`client/newsly/Maestro/`. Compatibility evidence that still explains persisted
data lives in the language-neutral corpus, SQLx baseline, initiative record, and
git history; there is no retired runtime tree in which to add new behavior.

## What the audit changed

Four findings were release prerequisites rather than parity targets:

1. Long LLM, image, crawler, storage, and E2B work no longer owns a database
   transaction. Rust prepares immutable input in a short transaction, releases
   the connection, performs external work, and finalizes through a fresh
   owner/lease/generation-fenced transaction.
2. Content and News use independent identity domains. A missing Content row can
   no longer fall back to an unrelated same-number News row.
3. Deployment now depends on a required quality workflow and consumes its exact
   tested SHA.
4. The Rust Utoipa inventory replaces the vacuous FastAPI typed-response test;
   export and drift checks require a real, known operation set.

The migration also corrected the contract rather than copying incidental
Python serialization:

- one generated wire boundary, including the Share Extension;
- one typed public error envelope;
- canonical News separated from legacy/long-form Content;
- deliberate absent, explicit-null, and default semantics;
- truthful pagination instead of reporting page length as a global total;
- typed UTC timestamps;
- privacy-safe telemetry before public route retirement;
- one onboarding discovery path;
- a generated, discriminated submission-status result whose content, feed
  subscription, Learning Deck, and no-action variants cannot carry each
  other's fields; temporary installed-client mirrors must agree with that
  canonical result until telemetry permits removal.

Safe subtraction is treated separately from public compatibility. The retired
Python runtime removes its unused metadata, reexports, and blanket import
surfaces with the runtime itself. Public routes still require runtime telemetry
and installed-client evidence before deletion. The authenticated iOS client now
has one explicit, instance-bound composition graph, and `SearchViewModel` owns
cancel-and-replace search work with stale-result fencing. Those client cleanups
are complete locally; deleting any remaining public compatibility endpoint is
still evidence-gated.

Five formerly oversized Rust implementation modules were split into cohesive
feature children below the repository ceiling, and their obsolete module-size
exemptions were removed. The guard now enforces the same limit on those modules
as on new Rust code.

`llm_tasks` is canonical for new Learning Deck work, but the Rust repository
still contains read-only `learning_deck_runs` compatibility projections and
cleanup paths for stored legacy decks. Removing that ledger remains gated on
production counts, backfill verification, and an explicit schema migration.

## Persistence and migration authority

The 91 Alembic revisions were frozen at `20260829_02` and their source tree was
then removed. Git history preserves the revisions; the first embedded SQLx
migration and contract corpus preserve the audited catalog. Alembic is not an
active migration system.

Fresh databases execute the baseline. Existing databases require an explicit
maintenance barrier and one adoption lock spanning head, catalog, bounded data,
role/grant, and exact SQLx-history verification; baseline recording; pending
migrations; and final verification. Only an empty history or an exact
checksum-matching prefix from the same embedded baseline is resumable. Gaps or
mismatches fail closed.

The final migration changes every live route, task, state writer, and E2B
namespace to Rust. Ownership history and durable work stamps remain as audit
and fencing evidence. A future authority change must still use acknowledged
two-phase promotion; it may never silently reassign in-flight work.

## Contracts, agents, and E2B

Public, domain, persistence, and provider types are separate. Serde owns
encoding, Utoipa owns public OpenAPI, and Schemars owns provider/tool JSON
Schema. SDK-native values are transient: Rig, `async-openai`, ConnectRPC, and
E2B types never become database or public API formats.

The Swift app and Share Extension remain generated OpenAPI clients. The Rust
CLI shares contract types directly and treats ordinary successful response
bodies as JSON so a new server enum value does not break an installed binary.
The retired generated CLI model artifact and checked CLI-specific OpenAPI
projection are not parallel contract authorities.

Persisted agent history uses a versioned Newsly transcript. Provider policy,
structured-output repair, dynamic tools, usage, cancellation, and resumable
response IDs are Newsly behavior around the replaceable Rig engine.

The direct E2B layer owns sandbox lifecycle, files, snapshots, network policy,
streaming commands, process reattachment, corpus hydration, template revision,
hardening, diagnostics, usage, and deletion. It preserves stream order and
incremental UTF-8, enforces bounds while streaming, and never blindly repeats a
command after ambiguous delivery.

## Python boundaries

The document extractor retains Crawl4AI and its surrounding static/browser
policy because that combination has materially better extraction behavior. Its
versioned request/result API is authenticated and bounded. The process has no
Newsly database, queue, JWT, Firecrawl credential, retry authority, or durable
state. Rust owns all persistence, Firecrawl fallback calls, usage, and follow-up
work.

Offline evals retain datasets, local SentenceTransformers and candidate models,
judges, cost controls, reports, and visualization. Versioned JSON/NDJSON
artifacts feed the real Rust matcher through `newsly-eval-driver`; Python does
not keep a shadow production algorithm or write Newsly state.

## Validation and release status

The settled backend candidate passes:

- warning-denied Clippy and all 360 tests across 17 packages in the full
  PostgreSQL-enabled Rust workspace; locked offline compilation also passes;
- fresh-database SQLx migration and prepare checks, plus a Rust API smoke against
  a throwaway migrated database with healthy `/health`, `/health/live`, and
  `/health/ready` responses;
- 39 Python-island tests (4 eval and 35 extractor) plus Ruff, formatting, MyPy,
  database-boundary, and package-build checks;
- Rust-owned public-contract regeneration/drift and canonical submission-result
  coverage;
- focused Rust `newsly-cli` command, transport, config, output, polling, and
  library-safety tests;
- all 632 native tests (629 unit and 3 UI), with zero failures or skips;
- local and production Compose configuration plus shell syntax checks.

Docker image builds could not run because this environment has no Docker daemon.

The disposable local E2E environment additionally proved:

- five scraper runs, 123 post-fix enrichment completions, 15 complete
  extraction-summary-image chains, and 97 ready News items;
- a live Crawl4AI public extraction, direct E2B lifecycle and agent execution,
  two-turn tool-backed chat, all four Share API modes, and atomic Add Feed
  config/backfill persistence;
- uncapped initial/repair Learning Deck policy plus a completed live run with
  9,442 output tokens and a signed, browser-validated viewer;
- authenticated full/ranged stored-audio delivery and AVPlayer completion;
- 55 valid AXe captures against the current checkout and disposable Rust
  backend, including chat, Share-driven additions, Learning Deck, and audio.

The disposable E2B sandbox, snapshot, and template were deleted after the
canaries. Production extraction/media/object-storage and account-deletion
canaries were not run.

The following still must happen before production release:

- existing-database SQLx adoption/interruption and remaining
  production-shaped PostgreSQL contention, lease, retry, and cross-feature
  parity tests;
- production media/object-storage and account-deletion canaries;
- production-shaped image build and exact-SHA deployment rehearsal where a
  Docker daemon is available;
- commit and push if requested;
- production writer drain, SQLx adoption, Rust authority migration, deployment,
  and live health/queue/cost/leak proof.

No production database was adopted, no ownership was promoted in production,
no deployment occurred, and no Apple distribution was performed as part of
this local migration work.

The external `willemave/newsbuddy` Homebrew tap still needs a separate formula
update before Homebrew installs distribute the Rust CLI. The repository source
install is `cargo install --locked --path rust/crates/newsly-cli`.

The detailed rationale is in [the design](10-design.md); package order,
canaries, and cutover procedure are in [the implementation plan](20-implementation-plan.md).
