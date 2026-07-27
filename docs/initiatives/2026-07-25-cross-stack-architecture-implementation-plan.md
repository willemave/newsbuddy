# Cross-Stack Architecture Implementation Plan — 2026-07-25

## Goal

Implement the verified Python and iOS architecture review findings without changing product
semantics accidentally, losing concurrent user work, or treating migration compatibility as dead
code before production state proves that it is safe to remove.

The intended end state is:

- one queue-specific Python worker graph instead of every process loading every handler;
- one backend chat-turn lifecycle and one iOS completion-status owner;
- pooled HTTP, bounded feed concurrency, batched ingestion, and targeted queue control-plane work;
- no request middleware that buffers large uploads;
- one active iOS root shell, with only the server enum retained for old-client compatibility;
- generated wire contracts mapped once into behavioral iOS domain models;
- one owner each for badge state, pagination cancellation, image downloads, and deck status;
- verified dead code removed, with compatibility paths retired only after live-state gates;
- focused tests after each package and full cross-stack gates at the end.

## Execution rules

1. Keep each implementation package well below the roughly 1,500-line human-review threshold.
   Large pure-deletion packages may exceed it only when the deleted surface is independently
   verified and the surviving call graph stays unchanged.
2. Do not mix migration cutovers with unrelated refactors.
3. Preserve the backend changes that appeared in the shared worktree before this plan was
   created. Overlapping files require a fresh diff review before editing.
4. Update this document when a package starts, completes, changes scope, or becomes blocked.
5. Every dead-code deletion requires a repository-wide symbol and string-dispatch search.
6. Performance changes need either a deterministic code-path regression test or before/after
   runtime evidence. Do not claim a speedup from source inspection alone.
7. No commit, push, deploy, production mutation, schema drop, or destructive data operation is
   implied by this plan.

## Status legend

- `TODO` — ready but not started
- `ACTIVE` — currently being implemented
- `DONE` — implemented and focused validation passed
- `GATED` — depends on production evidence, runtime profiling, or a preceding package
- `BLOCKED` — cannot proceed safely without new authority or unavailable evidence

## Task graph

```text
WP0 baseline + plan
 ├─ WP1 Python safe deletion ───────────┐
 ├─ WP2 iOS safe deletion ─────────────┤
 ├─ WP3 upload + worker composition ───┤
 │   └─ WP4 HTTP + scraper batching ───┤
 │       ├─ WP5 discussion providers ──┤
 │       └─ WP6 queue + storage ───────┤
 ├─ WP7 Python chat lifecycle ─────────┤
 ├─ WP8 Python ownership cleanup ──────┤
 ├─ WP9 iOS completion ownership ──────┤
 ├─ WP10 classic shell retirement ─────┤
 ├─ WP11 iOS stores/contracts/extension┤
 └─ WP12 iOS render/image/task perf ───┤
                                      ├─ WP13 production migration gates
                                      └─ WP14 complete validation + cleanup
```

## Work packages

### WP0 — Baseline, plan, and change ownership

Status: `DONE`

Goal: establish the exact shared-worktree baseline and keep concurrent edits out of the
architecture implementation diff.

Touch area: this plan, `git status`, current diffs, validation logs.

Validation:

- record current modified and untracked files;
- verify no iOS files are dirty before iOS edits begin;
- re-check overlapping backend files before every patch.

Review focus: no user-owned changes overwritten or reformatted incidentally.

Expected size: small.

### WP1 — Python confirmed-dead subtraction and guardrails

Status: `DONE`

Goal: remove only repository-proven dead Python paths and make the module-size guard useful.

Touch area:

- four broken news-pipeline evaluation scripts;
- unused Twitter GraphQL client while preserving canonical URL helpers;
- uncalled narration, compaction, workflow, and scraper wrappers;
- obsolete strategy protocol surface and unreachable image-generation branch;
- reverse onboarding facades;
- `config/module_size_guardrails.json` and its checker/CI hook.

Dependencies: WP0.

Non-goals: podcast task-type removal, Learning Deck worker removal, legacy news data cutover.

Validation: Ruff, Vulture delta, affected unit tests, module-size checker, script/doc reference
searches.

Expected size: medium, mostly deletion.

### WP2 — iOS confirmed-dead subtraction

Status: `DONE`

Goal: delete orphaned production UI/model clusters and unreachable leaf APIs without touching
the classic-shell product gate yet.

Touch area:

- NewsGroup model/view model/card and tombstone tests;
- LearningDeckListSheet/row cluster and structural tests;
- unused `ContentListViewModel.Mode.content` behavior;
- unused ChatService polling wrapper;
- write-only `CachedAsyncImage.isLoading`;
- exact-symbol-zero DTOs, endpoint helpers, and aliases.

Dependencies: WP0.

Non-goals: classic shell deletion and generated-contract redesign.

Validation: iOS dead-code structural tests, targeted `newslyTests`, Xcode compile.

Expected size: medium, mostly deletion.

### WP3 — Python upload safety and queue-specific worker composition

Status: `DONE`

Goal: stop buffering large uploads and stop every queue process from loading every handler and
LLM dependency.

Touch area: request logging middleware, sequential/threaded processor composition, handler
registries, gateway package imports, worker tests.

Dependencies: WP0.

Non-goals: queue persistence semantics and chat lifecycle.

Validation:

- multipart body is never read by logging middleware;
- bounded JSON logging behavior remains covered;
- every configured queue resolves exactly its supported handlers;
- fresh-process import-time/RSS comparison;
- worker tests and supervisor configuration tests.

Expected size: medium.

### WP4 — Canonical HTTP transport and batched scraper ingestion

Status: `DONE`

Goal: use pooled HTTP clients, bounded concurrent feed fetches, conditional requests where
supported, batched identity resolution, one persistence transaction per scraper batch, and bulk
queue notification.

Touch area: `app/services/http.py`, `app/http_client/`, feed scrapers, scraper runner/base,
news identity resolution, queue insertion helpers.

Dependencies: WP3 handler/import boundaries stable.

Non-goals: changing extraction or feed-selection product behavior.

Validation: 5xx retry regression, timeout regression, request reuse tests, query-count tests,
scraper success/failure isolation tests.

Expected size: several small stacked increments; do not implement as one diff.

### WP5 — One discussion-provider layer

Status: `DONE`

Goal: share Hacker News and Reddit fetch/normalization implementations while retaining separate
persistence callers only where required.

Touch area: `discussion_fetcher.py`, `news_item_discussions.py`, discussion tests.

Dependencies: WP4 HTTP boundary.

Non-goals: changing ranking or presentation semantics.

Validation: normalized provider fixtures, two-request HN tree behavior, malformed/retry tests.

Expected size: medium.

### WP6 — Queue retention, targeted control-plane queries, and storage accounting

Status: `DONE`

Goal: prevent historical task growth from making every worker poll expensive and prevent
object-store reads from synchronously committing one telemetry row each.

Touch area: queue service/model indexes, retention command, vendor usage recording, object
storage gateway, admin visibility/tests.

Dependencies: WP3; production table-size and query-plan evidence before final retention value.

Non-goals: deleting active task history or changing retry semantics without migration coverage.

Validation: targeted aggregate SQL tests, bounded retention/commit tests, `EXPLAIN ANALYZE`
evidence, and storage request-accounting tests. Provider-native metrics remain authoritative for
complete request metering; the application ledger records mutations only because it has no
storage-pricing model or reliable multiprocess metrics sink.

Expected size: medium.

### WP7 — One Python chat-turn lifecycle

Status: `DONE`

Goal: centralize load, detached external work, persistence, ledger transitions, usage, and
failure handling for chat, assistant, and suggestion turns.

Touch area: `chat_turn_runtime.py`, `chat_agent.py`, `assistant_router.py`, chat command/router,
tests.

Dependencies: WP0.

Non-goals: changing prompts, tools, model selection, or public response payloads.

Validation: characterization tests for every current entrypoint, session-lifetime regression,
success/failure/usage ledger tests.

Expected size: multiple medium increments.

### WP8 — Python ownership cleanup

Status: `DONE`

Goal: make documented layers real: a decomposed onboarding package, one summary-kind adapter,
and router mutations owned by commands/queries.

Touch area: onboarding modules, summary metadata/contracts/domain adapter, direct-DB routers,
tests and patch seams.

Dependencies: WP1 deletion and WP7 lifecycle ownership.

Non-goals: data-model migration or externally visible contract changes.

Validation: characterization tests first, then Ruff and focused API/service suites.

Expected size: separate medium increments by subsystem.

### WP9 — One iOS message/deck completion owner

Status: `DONE`

Goal: replace foreground/background/service/reader polling duplication with actor-backed keyed
status stores and adaptive polling; allow multiple observers without duplicate requests.

Touch area: ChatService, ChatSessionViewModel, ActiveChatSessionManager,
LearningDeckReaderViewModel, LearningDecksViewModel, tests.

Dependencies: WP2 removes the unused service wrapper first.

Non-goals: adopting SSE or WebSockets unless the existing API already exposes a suitable stream.

Validation: virtual-clock polling tests, foreground/background handoff, multiple-observer
coalescing, cancellation, request-count assertions.

Expected size: medium-high, split chat and deck status into separate increments.

### WP10 — Retire the classic iOS shell

Status: `DONE`

Goal: make Briefing the only composition root and delete classic-only views, view models,
navigation paths, badge plumbing, and root-tab cases.

Touch area: Maestro/E2E flows first, then ContentView, RootTabs, tab coordinator/factory,
classic-only files/tests.

Dependencies: baseline E2E failures recorded; every required flow migrated to Briefing.

Non-goals: changing Briefing product behavior or deleting the server enum in the same package.

Validation: focused unit tests, complete native tests, affected Maestro flows, simulator
navigation/read-state verification.

Expected size: one E2E migration increment plus one large but mechanical deletion increment.

### WP11 — iOS store, pagination, contract, and extension boundaries

Status: `DONE`

Goal: one badge store/lifecycle owner, one pagination cancellation owner, one feature-network
boundary, generated wire decoding with explicit domain mapping, and a minimal shared extension
transport module.

Touch area: badge coordinator/wrappers, PaginatedFeed/FeedLoadTaskRunner, repositories/services,
Learning Deck DTO mapping, Xcode target membership, Share Extension.

Dependencies: WP2 and WP10 reduce the consumers first.

Non-goals: removing behavioral domain models or changing endpoint payloads.

Validation: store lifecycle tests, pagination race tests, contract fixtures, clean main-app and
Share Extension builds with compile-source evidence.

Expected size: several independent small-to-medium increments.

### WP12 — iOS render, image, and task-lifetime performance

Status: `DONE`

Goal: remove repeated render work and duplicate/outliving async work on active production paths.

Touch area:

- cached Knowledge projections;
- stable hero image request identity;
- one URL-keyed raw image loader and structured bounded prefetch;
- keyed detail-body tasks and source fallback reuse;
- lazy Briefing render-model materialization;
- Briefing passage scaling cache;
- narrow Observation for Briefing and Dig;
- markdown render cache and collection-scoped animations where measurements justify them.

Dependencies: independent items may start after WP0; Briefing observation follows
characterization tests.

Non-goals: replacing the pager or custom tab bar without before/after evidence.

Validation: unit/request-count tests, existing signposts, Release Instruments captures for the
named interactions, full native compile/tests.

Expected size: separate small/medium increments by performance mechanism.

### WP13 — Production migration gates and compatibility retirement

Status: `DONE`

Goal: inspect live queue and database state, then remove only compatibility code proven unused.

Touch area: read-only admin/DB evidence first; podcast task types/handlers, Learning Deck worker
and compatibility reads, legacy news bridge only after its own data cutover.

Dependencies: relevant replacement paths and regression coverage complete.

Non-goals: production repair or destructive data mutation during evidence collection.

Validation: saved query/log evidence, zero active legacy tasks/rows or an explicit migration,
focused backend and client tests.

Expected size: separate packages per compatibility domain.

### WP14 — Complete validation and post-edit cleanup

Status: `DONE`

Goal: run the full valid gates, inspect only the architecture implementation diff for slop and
duplication, and update this plan with final status and deferred evidence-gated items.

Dependencies: all implemented packages.

Validation:

- Ruff on every touched Python file and full configured Ruff gate;
- focused suites after every package and complete valid Python suite at closeout;
- native iOS tests and Share Extension build;
- affected Maestro flows;
- module-size guard and contract-generation checks;
- `git diff --check`, diff-size review, and final worktree ownership audit.

Expected size: validation and narrow cleanup only.

## Execution log

- 2026-07-25 — Review completed. Core modular-monolith and SwiftUI directions retained; the
  implementation focuses on subtraction, canonical ownership, and measured hot paths.
- 2026-07-25 — Plan created. WP0 active. Shared worktree already contains unrelated backend
  edits; those remain user-owned and must be preserved.
- 2026-07-25 — WP0 complete. The concurrent backend work landed on `main` as `97aafd0f`
  (`fix(workers): close threaded runtime review gaps`) while the plan was being written. The new
  execution baseline is `main@97aafd0f`; the worktree is clean apart from this plan document.
- 2026-07-25 — WP1, WP2, and WP3 started as disjoint implementation lanes.
- 2026-07-25 — WP12 started with isolated active-path increments: detail-body tasks are now
  keyed/cancellable and reuse an in-flight or loaded source fallback; Briefing and Dig migrated
  from broad `ObservableObject` invalidation to Observation; passage scaling gained a revision
  cache/signpost; selectable markdown gained a bounded shared render cache; chat collection
  animations were narrowed to the changing collection/indicator.
- 2026-07-25 — First focused iOS test build compiled the WP12 sources and tests, then stopped on
  the expected transient WP2 state: NewsGroup production files had been deleted while their test
  references were still being removed. Re-run after WP2 completes.
- 2026-07-25 — WP2 complete: deleted the NewsGroup and old Learning Deck list clusters, removed
  the unused ChatService polling wrapper and `CachedAsyncImage.isLoading`, and reduced
  `ContentListViewModel` to Knowledge and Recently Read. Net production reduction: 1,285 lines.
  Twenty-one focused native tests and fourteen structural tests passed.
- 2026-07-25 — WP12 combined follow-up passed fifteen focused native tests covering detail task
  cancellation/source reuse, passage revision caching, shared markdown caching, and existing
  passage routing/typography behavior.
- 2026-07-25 — WP3 complete. Request logging now buffers only declared JSON or URL-encoded bodies
  up to 64 KiB; large/streaming/unsupported bodies are metadata-only. Queue processors compose
  handlers lazily from canonical task specs and initialize the summarizer only for queues that
  need it. Fresh import improved from 2.26–2.90 seconds / about 348 MB RSS to 0.94–1.22 seconds /
  about 161 MB RSS. The worker/request slices passed 281 tests, plus Ruff and compile checks.
- 2026-07-25 — WP1 complete. Removed 1,984 lines of repository-proven dead Python, including
  broken evaluation scripts, the unused Twitter GraphQL implementation, obsolete workflow and
  scraper wrappers, and unreachable narration/compaction helpers. Six module-size ratchets now
  report missing and oversized targets together and run in both the architecture guard and deploy
  CI. Eighty-three focused tests, Ruff, compile/import checks, and script/config validation passed.
- 2026-07-25 — WP4 began with the canonical pooled HTTP/feed-fetch increment; scraper persistence
  batching remains a separate follow-up. WP9 began with the keyed chat-message completion owner;
  Learning Deck status remains a separate follow-up.
- 2026-07-25 — WP12 image/render increment complete. Hero overscroll no longer changes image
  request identity; requested sizes use stable 128-pixel buckets; raw transfers coalesce by URL
  across decode variants; prefetch is capped at four concurrent downloads; and Knowledge computes
  its ready-item projection once per render rather than once per row. Three native image-cache
  request/concurrency tests and thirty-nine iOS structural tests passed.
- 2026-07-25 — WP12 Briefing materialization increment started. Snapshot-restored and prefetched
  background lenses retain their API document without building attributed render models; the
  selected lens materializes on demand and non-selected read-state updates invalidate rather than
  rebuild dormant presentation state.
- 2026-07-25 — WP10 E2E migration started. Content-detail save, discussion, and Council flows now
  enter through the existing deterministic content route instead of the Long/Fast tabs. The last
  list-specific read test now exercises Briefing's category read command against a seeded lens.
- 2026-07-25 — WP4 transport/feed increments complete. `HttpService` now owns reusable normal and
  SSL-relaxed clients with explicit close/reset lifecycle; 5xx and transport failures retry while
  4xx failures do not. Atom, Substack, podcast, and RSS-cluster parsers consume bytes from the
  shared fetch boundary and retain per-feed failure isolation. Ninety-three focused HTTP/scraper
  tests and seventy-six additional impacted tests passed. Persistence/query batching is active as
  the next isolated increment.
- 2026-07-25 — WP11 pagination increment started. `PaginatedFeed` now owns and cancels the actual
  page-load task when a refresh supersedes it or the feed resets, while generation checks remain as
  the stale-result backstop.
- 2026-07-25 — WP9 chat-completion increment complete. One message-ID-keyed actor now coalesces completion observers across
  foreground Chat, background active-session tracking, and Learning Deck chat; it supports
  cancellation handoff without tombstones, caches only terminal outcomes, and uses an adaptive
  one-minute policy capped at thirty-six requests instead of 120. Five exact-version registry tests
  plus the affected Chat/Learning lifecycle suites passed. The separate Learning Deck generation
  and viewer-status loops remain as the second WP9 increment.
- 2026-07-25 — WP7 shared-runtime increment complete. Background article Chat and contextual
  Assistant turns now use one detached runtime for session snapshotting, database release before
  provider work, ledger/usage transitions, staged success or failure persistence, timestamps, and
  cleanup. Ninety-seven focused lifecycle tests, Ruff, import/compile smoke, the module-size guard,
  and diff checks passed. Council, initial-suggestion, deep-research, and seed-message paths retain
  their intentionally different orchestration and remain to be assessed as separate increments.
- 2026-07-25 — WP9 Learning Deck increment implemented and awaiting a stable iOS build lane. One
  deck-ID-keyed actor now coalesces list and reader observers, supports cancellation handoff and
  invalidation for regeneration, and preserves the six-minute window with eighty adaptive requests
  instead of 120. Its first focused build was blocked by transient stale `RootTab` references while
  WP10's deletion increment was still in flight; rerun after WP10 reaches a compiling state.
- 2026-07-25 — WP10 complete. Briefing, Knowledge, and Learning are now the only root tabs and
  More remains a sheet; local Classic selection/restoration, feed roots, presentation policy,
  view models, helpers, and obsolete narration-creation UI were removed while the server's
  `classic` enum remains wire-compatible. The package deleted twenty-three files and 3,639 lines.
  The app and embedded Share Extension build succeeded, thirteen focused native tests and
  fifty-two structural checks passed, and the architecture guard passed. Four migrated Maestro
  flows are collected but cannot run on this machine until a Java runtime is installed.
- 2026-07-25 — WP9 complete. The Learning Deck registry now joins the chat-message registry as a
  single keyed polling owner: list and reader observers share requests, cancellation handoff is
  safe, regeneration and deletion explicitly invalidate state, and the six-minute policy uses at
  most eighty adaptive requests instead of 120. Three registry tests plus the deck list, reader,
  reliability, and chat lifecycle suites passed.
- 2026-07-25 — WP11 advanced through three independent increments. `PaginatedFeed` owns and
  cancels superseded requests; Learning Deck wire DTOs are generated contracts mapped once into
  non-Codable domain behavior; and the Share Extension compiles against a narrow authenticated
  transport instead of the main app's API/settings graph. The integrated run passed seven
  pagination tests, three mapping tests, four extension transport tests, and all selected deck,
  Briefing, detail-task, and image-cache regressions. Both the main app and embedded extension
  build successfully.
- 2026-07-25 — WP4 complete. Feed scrapers run with a deterministic four-feed concurrency cap;
  identity, inbox status, discussion eligibility, and queue insertion are batch-oriented; and one
  caller-owned transaction emits one PostgreSQL notification per scraper batch. On a real local
  PostgreSQL run with three new items, long-form ingestion fell from 21 to 7 SQL statements and
  seven to one commits; news ingestion fell from 42 to 8 statements and ten to one commits. The
  complete 144-test scraper/service/pipeline slice, Ruff, compile, architecture, and diff checks
  passed. PRAW-backed Reddit fetching remains serial because its shared client's thread safety is
  not established.
- 2026-07-25 — WP6 production gate opened with read-only evidence. Production has 774,506 task
  rows: 709,350 completed, 65,155 failed, and one pending. The table occupies 167 MB plus 304 MB
  of indexes; 702,653 terminal rows are older than seven days, and no `retry_count` or
  `available_at` values are null. Retention and targeted control-plane work are now active; no
  production rows were changed. Separately, S3-compatible reads and probes no longer synchronously
  write one usage row each, and writes return known size metadata without a redundant HEAD call;
  twelve gateway tests and Ruff pass.
- 2026-07-25 — WP5 complete. Hacker News and Reddit acquisition/normalization now live in one
  provider module while the two callers retain their intentionally different persistence shapes.
  HN uses the pooled HTTP boundary for its Firebase metadata and Algolia tree requests; Reddit
  preserves the cached PRAW client and retry classification. Private cross-module imports were
  removed, in-batch discussion deduplication was preserved, and ninety-one provider/consumer tests,
  Ruff, compile, module-size, and diff checks passed.
- 2026-07-25 — WP8 summary ownership increment complete. Runtime callers now use `SummaryKind`
  and `SummaryVersion` directly, legacy inference lives with the summary contracts, and one domain
  projection module owns summary flattening. `ContentData` fell from 411 to 189 lines and now
  retains validated Article/Podcast metadata rather than returning the original unchecked mapping.
  One hundred thirteen focused tests, the public-contract and module-size guards, full Ruff, import,
  format, and diff checks passed.
- 2026-07-25 — WP8 chat-router ownership increment complete. Update, archive, and contextual
  assistant-turn mutations now live in application commands; the FastAPI module is reduced from
  685 to 455 lines and only adapts transport inputs/outputs for those paths. Eighty-four chat,
  assistant, history, lifecycle, and router tests plus Ruff and compile checks passed.
- 2026-07-25 — WP6 complete. A shared 14-day terminal-task predicate now drives a dry-run-first,
  `--yes`-guarded cleanup command and daily cron; backpressure uses one targeted content-queue
  aggregate; dequeue predicates align with non-null schema invariants and replacement indexes are
  created concurrently before a short name swap. A real PostgreSQL upgrade/downgrade passed, along
  with ninety focused tests, the architecture guard, Ruff, compile, Alembic single-head, and diff
  checks. The migration and cleanup were not applied to production; the first cleanup will need
  operational scheduling because it can generate substantial WAL and dead tuples.
- 2026-07-25 — WP8 remaining router mutations complete. Discovery dismissal and clear now use one
  owner-filtered command (with a set-based `UPDATE ... RETURNING` for clear), and news-item article
  conversion delegates to the existing conversion command while preserving visibility, nested
  title precedence, stored-body reuse, queue selection, Knowledge save, and response contracts.
  Forty-three relevant tests plus focused format, Ruff, compile, contract, and diff checks passed.
- 2026-07-25 — WP8 eval-helper increment complete. Admin eval, fixture summary eval, and prompt
  debugging share prompt routing, current pydantic-ai output extraction, and news-context rendering;
  the legacy `result.data` fallback and duplicated helper bodies are gone. Nineteen focused tests
  and Ruff pass.
- 2026-07-25 — WP13 read-only compatibility evidence collected. Legacy podcast queue types have no
  active rows and were last used on 2026-04-02; legacy Learning Deck queue rows are terminal and the
  six historical `learning_deck_runs` rows are all completed or failed. Their drained queue shims
  are being retired while historical Learning Deck read projections remain. The legacy news bridge
  is still gated: all 25,693 production `contents` news rows are unbridged, so that compatibility
  path cannot be removed safely. Both production users already store `reading_experience=briefing`,
  but the server enum remains an old-client compatibility boundary rather than a dead DB path.
- 2026-07-25 — WP11 complete. A single observable `BadgeStatsStore` now owns the rendered
  long-form unread and processing badge state, coalesced refreshes, active-processing scheduling,
  app/auth lifecycle, reset/suspension, and local read-count mutation; the coordinator and two
  wrapper stores were deleted. The root shell no
  longer observes badge values, so a count change does not invalidate the whole app composition.
  Combined with the completed pagination, Learning Deck contract, and Share Extension increments,
  nine focused badge/read-cache tests, fifty-five iOS structural tests, the architecture guard,
  main-app compile, and embedded-extension compile pass.
- 2026-07-25 — WP7 complete. Initial article suggestions joined background article Chat and
  contextual Assistant on the detached turn runtime: request sessions close before provider work,
  assistant-only transcript shape is preserved, and success/failure ledger, usage, cleanup, and
  persistence semantics share one lifecycle. Router-private read-model aliases are gone. Council
  remains a multi-branch orchestrator and deep research remains its dedicated workflow rather than
  being forced through a false abstraction. Eighty-seven focused lifecycle and router tests pass.
- 2026-07-25 — WP8 complete. Onboarding's 2,451-line implementation was physically decomposed
  into ten ownership modules plus a 90-line public facade; the largest implementation module is
  489 lines, `_core.py` and reverse facades are gone, and an ownership/API regression prevents
  recombination. The complete 216-test onboarding/router/integration/Briefing slice, full Ruff and
  format gates, imports, evaluator smoke, contracts, Vulture, and architecture guard pass.
- 2026-07-25 — WP13 compatibility retirement complete. Production-drained `download_audio`,
  `transcribe`, and `generate_learning_deck` task contracts/handlers and the empty Learning queue
  topology were removed; podcast media now has one worker path. Historical Learning Deck read
  rows remain, and the current LLM-task generation path remains intact. The package removes about
  1,280 lines and passes 197 pipeline tests plus compatibility, topology, contract, Go, and
  compile checks. The news bridge remains by design because 25,693 production news rows still
  require it; the server `classic` enum remains an old-client wire boundary.
- 2026-07-25 — Final dead-code audit removed the legacy Atom/Substack YAML loaders, ignored
  scraper `config_path` parameters, obsolete warning/filename helpers, an unreferenced LLM-task
  proposal function, and the now-orphaned scraper metrics/error module. High-confidence Vulture
  findings are empty; its remaining 60-percent reports are framework fields and lazily registered
  handlers, plus the intentional gateway package `__getattr__` hook. Sixty-one focused tests pass.
- 2026-07-25 — WP12 implementation complete. In addition to the earlier render/image/task work,
  deterministic URL prefetch order is preserved, non-cancellation deck poll sleep failures finish
  every observer, and the root uses the modern `Tab(…)` builder without forcing a visual replacement
  of the measured custom bar. The full native gate passes 370 unit tests plus the launch UI test,
  and the embedded Share Extension builds. Physical-device SwiftUI/Time Profiler comparison and
  actual Maestro execution remain environmental verification gates; no frame-time claim is made.
- 2026-07-25 — Production query-plan verification exposed an admin tooling bug: `db explain`
  emitted SQLite's `EXPLAIN QUERY PLAN` against PostgreSQL. It now selects the database-native
  prefix, accepts only `EXPLAIN` targeting one read-only `SELECT`/`WITH`, and keeps PostgreSQL
  transactions read-only while allowing SQLite diagnostics. Fourteen admin tests pass. Production
  still runs the old CLI until a later authorized deployment; no production code or data changed.
- 2026-07-25 — WP6 rollout safety tightened after the production row count: retention now commits
  5,000-row batches and stops after 50,000 rows per daily run instead of placing roughly 700,000
  historical deletions in one transaction. The initial backlog will drain over multiple days,
  limiting lock duration and WAL bursts; dry-run and explicit `--yes` safeguards remain.
- 2026-07-25 — Final low-confidence dead-code adjudication removed two settings with no runtime,
  environment-template, script, or test consumer. Dynamic per-queue worker fields, Pydantic/ORM
  fields, public enum members, migrations, lazy handler classes, and the gateway export hook remain
  because their consumers are runtime dispatch, persisted/API data, or framework discovery rather
  than direct Python references. A meaningless dynamic `error_logger` test attribute was replaced
  with assertions against the actual Substack logger boundary.
- 2026-07-25 — WP14 complete. The final tree passes 2,266 Python tests (17 Maestro tests skipped),
  the 106-test architecture guard, Ruff lint and formatting across 631 Python files, high-confidence
  Vulture, bytecode compilation, public-contract regeneration checks, the 15-file module-size guard,
  one-head Alembic validation, Go CLI tests, shell syntax checks, and `git diff --check`. The final
  native run passes 371 unit tests plus the launch UI test and builds the embedded Share Extension;
  its result bundle is
  `/tmp/newsly_arch_final_20260725_1/Logs/Test/Test-newsly-2026.07.25_19-22-04--0700.xcresult`.
  Maestro execution remains skipped because Java is not installed, and physical-device
  SwiftUI/Time Profiler comparison remains unavailable; both are recorded as environmental
  verification gates rather than unsupported performance claims. No production migration,
  retention cleanup, deployment, commit, or push was performed.
- 2026-07-26 — Thermo-nuclear follow-up complete. Scraper batch persistence now falls back to
  isolated per-item transactions after a failed fast-path batch; `PaginatedFeed` propagates caller
  cancellation into its owned request; badge refresh failures retain polling while previously
  known processing is active; and Atom, Substack, and podcast scrapers use direct per-feed workers
  instead of recursively invoking a hidden `scrape(_feeds:)` mode.
- 2026-07-26 — Ownership follow-up complete. Chat and Assistant now compose explicit
  prepare/execute/persist primitives instead of a seventeen-argument callback runner, and initial
  suggestions accept a session ID so database-session ownership is local and visible. Queue
  enqueue/dedupe, retention, and metrics moved out of the 1,213-line service; the remaining facade
  is 635 lines. `TaskSpec` is now the single source for handler imports and summarizer requirements,
  and `TaskContext` exposes a typed, required summarizer boundary rather than `Any`.
- 2026-07-26 — iOS polling and growth-guard follow-up complete. Chat-message and Learning Deck
  registries share one keyed observer store while preserving their domain policies. The module-size
  guard now automatically covers all non-generated Python and Swift source files and retains
  explicit ratchets for known large modules. Final validation passed 2,273 Python tests (17 Maestro
  tests skipped), 107 architecture/contract tests, Ruff lint and formatting across 634 files, 374
  native iOS tests including launch, the Share Extension simulator build, compileall, and diff
  checks. No commit, push, deployment, migration, retention cleanup, or production mutation ran.
- 2026-07-26 — Post-edit cleanup complete. Callback-era chat persistence parameters and duplicated
  tool-name extraction are gone; task behavior flags are named; the initial-suggestions route uses
  a fresh response session; scraper queue writes have one same-transaction batch path; queue dedupe
  lookups select only keys and IDs; empty scrapes skip persistence; and the unreachable podcast
  fallback was removed. The iOS badge store no longer observes or mutates removed Fast Reads badge
  state, and polling registries shed tautological checks and an unnecessary cache scan. Synthetic
  test bookkeeping and order-sensitive concurrent-feed mocking were also removed. Final validation
  passes 2,274 Python tests (17 Maestro tests skipped), the 107-test architecture/contract gate,
  Ruff lint and formatting, high-confidence Vulture, 25 focused native iOS tests, module-size and
  diff checks. No commit, push, deployment, migration, retention cleanup, or production mutation
  ran.
- 2026-07-26 — Second post-edit cleanup pass complete. Task specifications now derive their keyed
  registry from one declaration; successful chat paths no longer carry dead optional result or
  timing initialization; scraper persistence uses one guarded queue call and a boolean instead of
  retaining inserted objects; repeated scraper-test transaction setup is shared; and queue metrics
  shed redundant integer fallbacks. iOS pagination uses its owned request task as the sole loading
  truth, badge component state and lifecycle helpers are private, read-count callers rely on the
  store's existing zero guards, cached chat outcomes use Swift `Result`, and test-only polling
  aggregates are gone. Focused validation passes 106 Python tests, the 107-test architecture and
  contract gate, Ruff lint/format, compile and diff checks, and 25 native iOS tests. A proposed
  podcast enclosure fallback removal was reverted after focused tests proved that FeedParser's
  mapping projection is required. No commit, push, deployment, migration, retention cleanup, or
  production mutation ran.
