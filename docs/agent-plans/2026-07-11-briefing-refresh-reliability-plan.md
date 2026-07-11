# Briefing refresh reliability and bounded-cache plan

Date: 2026-07-11  
Status: implemented and verified locally

## Outcome

Restore the original Briefing architecture: one activation owner, one per-user global
briefing version, stale-while-revalidate rendering, selected-lens-first loading, bounded
neighbor prefetch, and manual refresh implemented as enqueue plus bounded version polling.

The immediate incident symptom—`Network error: cancelled` replacing the whole Briefing—is
one consequence of a broader client-state and request-ownership regression. The fix must make
cancellation harmless, retain usable content during recoverable failures, and stop downloading
the entire edition merely to warm memory or populate a disk snapshot.

This plan deliberately keeps one global version per user. It does not add per-lens revisions,
content diffs, or a delta endpoint.

## Confirmed pre-implementation behavior

The July 11 investigation established the following:

- `BriefingViewModel.pullToRefresh()` mapped every thrown error, including cancellation, to the
  global `.error` state. A cancelled operation therefore hid valid cached content behind a
  full-screen error.
- Cancellation was already classified correctly by `isNetworkCancellation`; the manual-refresh
  path was the inconsistent caller.
- Briefing activation had two owners: `BriefingView.task` and
  `TabCoordinatorViewModel.handleTabEntered()`. Concurrent requests could coalesce, but closely
  spaced sequential activations could still perform duplicate index work.
- `prefetchRemainingLenses()` started every missing or stale lens request. The original design
  loaded the selected lens and bounded neighbors.
- The production edition inspected during the incident had 12 active lenses, 247
  active/degraded segments, and 1,059 source references. Entering or refreshing Briefing could
  transfer about 2.84 MB across all lenses. Individual lens responses took roughly 2.1–17.9
  seconds and reached about 365 KB.
- Manual refresh performed `POST refresh -> sleep 350 ms -> one forced index GET`.
  The observed worker task waited about 14 seconds to start, so the one-shot check commonly
  occurs before the requested refresh runs.
- A forced index GET returned a body even when the version did not change. The client then marked
  every lens stale and refetched all of them.
- The disk snapshot could grow to roughly the same size as the fully prefetched edition. Its write
  was dispatched off the main queue, but `Data(contentsOf:)` and JSON decoding during restore were
  synchronous on the main actor.
- The snapshot was one shared `briefing-snapshot.json`, cleared on logout. It was not
  structurally scoped to the authenticated user.
- The queue worker used `release_db_during_compose=True`. That production path set
  `compacted = 0`, so configured segment compaction never ran there.
- The existing non-release compaction helper covered only the first compaction window and then
  marked all donors compacted. Enabling it unchanged could remove unread source coverage.

The exact producer of the real-device URL cancellation remains unproven. Briefing's lens page
does use SwiftUI's `.refreshable` modifier, so cancellation of that structured task during view or
app lifecycle changes is a plausible producer, alongside URLSession and task replacement.
The design makes all of them safe and adds enough diagnostics to identify the source later.

## Implementation result

Phases 1–4 are implemented in the local checkout. The client now treats cancellation as neutral,
uses one lifecycle owner, preserves stale content, loads only the selected lens and pager
neighbors, stores async per-user snapshots, and polls the global version after an accepted manual
refresh. The server emits user-scoped validators, the release-DB worker performs all-or-nothing
coverage-preserving compaction, and `admin briefing status` reports segment-cap and payload-health
signals.

Phase 5 remains deliberately deferred. Pagination is conditional on post-deployment production
measurements; adding it before the 300 KB/latency gate is evaluated would expand the API without
evidence that bounded loading plus compaction is insufficient.

Local verification completed with 33 focused Briefing tests, the full 264-test native iOS suite,
and 26 focused backend/admin tests. An AXe run started a manual refresh, switched away and back
through the lifecycle boundary, and confirmed the Briefing content remained visible with no
full-screen error. The structured log recorded the refresh poll cancellation as neutral and the
next activation performed one index revalidation.

## Locked decisions

### One global version per authenticated user

`briefing_states.version` remains the sole application-level change counter. It is global across
that user's index and every lens, not global across users.

The contract is:

```text
same user + same briefing version = same user-visible Briefing representation
```

Every change visible through the index or a lens must bump the user's version once. This includes
refresh composition, retirement, compaction, read-state changes, taxonomy/lens changes, and
discussion/source enrichment that changes a returned lens payload.

The implementation must audit those mutation paths and replace the current test that treats a
same-version body as changed. If the same-version invariant is violated, that is a server bug—not
a reason for the client to diff payloads.

### User-scoped HTTP validator

The ETag remains the cheap revalidation mechanism, but it must be an opaque validator derived
from the authenticated user and current global version. `W/"v43"` alone is insufficient:
two different users can both be at version 43 while having different feeds.

Requirements:

- two users at the same numeric version receive different ETags;
- an unchanged request for the same user returns `304`;
- a version change—which is required for any representation change—returns `200` with a new
  ETag;
- the response is marked `Cache-Control: private, no-cache` and varies by authorization;
- an old stored `W/"vN"` validator naturally misses once during migration and receives `200`.

This does not introduce a second revision system. The version is the application change token;
the ETag is its user-scoped HTTP encoding. A `200` received during validator migration with the
same numeric version updates the stored ETag but does not imply a second kind of content change.

### Global invalidation, bounded reloading

When the authenticated user's global version changes, all cached lens payloads become logically
stale. They remain visible, but the client reloads only the active working set:

1. selected lens immediately;
2. previous and next lenses in the active pager, when present;
3. all other lenses only when selected or approached.

There will be no per-lens diff calculation.

### Per-user snapshots are caches, never authority

Snapshots exist only to paint usable content before networking completes. The server remains
authoritative, and every restored snapshot is revalidated. Snapshot I/O failure, expiry, or
cancellation is never a user-visible Briefing error.

## Required invariants

1. Cancellation never enters a user-visible failure state.
2. Usable content is never replaced by a full-screen networking error.
3. Full-screen failure is reserved for an initial load with no usable snapshot or cached index.
4. Authentication failures continue through the app-wide authentication path.
5. At most one activation/index revalidation owner exists.
6. Manual refresh has higher priority than background revalidation and prefetch.
7. A response from an obsolete request generation cannot overwrite newer client state.
8. A lens is fresh only for the currently accepted global Briefing representation.
9. A global change marks all cached lenses stale but does not eagerly fetch all lenses.
10. Snapshot data can never cross authenticated-user boundaries.
11. Snapshot work never causes network fan-out.
12. Compaction never removes an unread source from the active edition unless that source is
    represented by a successfully persisted replacement segment.

## State model

Replace the single overloaded `LoadState` with independent state:

```swift
contentPhase: idle | initialLoading | ready | empty | fatalError
refreshPhase: idle | requesting | waitingForVersion | failed
lensPhase[key]: missing | cached | loading | fresh | stale | failed
```

The exact names may follow local conventions, but the separation is required.

Error presentation follows this matrix:

| Situation | Result |
|---|---|
| Cancellation anywhere | No error; retain existing state |
| Revalidation fails with cached content | Keep content; log nonfatal failure |
| Manual refresh fails with cached content | Keep content; show action-level retry/message |
| Selected lens fails with stale payload | Keep stale page; show page-level retry if needed |
| Selected lens fails with no payload | Page-level failure, not whole-tab failure |
| Background neighbor prefetch fails | Silent to UI; retry when selected |
| Initial index fails with no snapshot | Full-screen initial-load error |
| Snapshot read/write/decode fails | Ignore, log, and fetch normally |

## Hand-computed target flows

### Cached activation with a newer server version

```text
t0  client = {user: 1, snapshot: v42, selected: technology, content: ready}
    server = {user: 1, version: 43}

t1  the one activation owner restores user 1's partial snapshot off-main
    client = {index: v42, technology: cached, content: ready}

t2  client sends GET /briefing with user 1's stored ETag
    server returns {status: 200, index: v43, new user-scoped ETag}

t3  client accepts the latest request generation, installs index v43, and marks every
    cached lens stale without removing its payload
    client = {index: v43, technology: stale-visible, other cached lenses: stale-visible}

t4  client fetches technology first and at most its two pager neighbors
    every other lens remains request-free until approached

t5  a lens response matching the accepted global representation becomes fresh;
    mismatched/obsolete responses are dropped and trigger index revalidation
```

### Manual refresh that is cancelled

```text
t0  client = {content: ready(v43), refresh: idle}

t1  manual refresh starts
    client = {content: ready(v43), refresh: requesting}

t2  low-priority prefetch is cancelled, pending read marks are flushed, and POST refresh
    is attempted

t3  URLSession or task lifecycle returns cancellation
    client = {content: ready(v43), refresh: idle}
    UI remains on the briefing; diagnostic event records the cancellation boundary
```

The invariant that failed in the current implementation—valid content becoming `.error` at
`t3`—can no longer occur because content and operation state are independent.

### Accepted refresh whose worker starts later

```text
t0  POST /briefing/refresh returns {accepted/enqueued, baselineVersion: 43}
    client = {content: ready(v43), refresh: waitingForVersion}

t1  bounded ETag polling returns 304 while the queue task waits
t2  app action/gesture is no longer blocked; polling remains VM-owned
t3  worker commits version 44
t4  next poll returns index v44 and a new ETag
t5  client marks global lens cache stale, reloads selected + neighbors, then ends refresh
```

If the poll deadline expires, the result is neutral: the request was accepted but no new version
was observed. The next activation revalidation catches a later completion.

### Account switch at an equal numeric version

```text
t0  user 1 snapshot = {userID: 1, version: 43, etag: A}
    user 2 server   = {userID: 2, version: 43, etag: B}

t1  user 2 receives a new VM/store scoped to userID 2
t2  user 1's snapshot path and envelope are rejected for user 2
t3  even if ETag A is accidentally sent, server compares it with B and returns 200, not 304
```

The user ID is enforced in the store path, snapshot envelope, dependency wiring, and validator.

## Phase 1 — Client correctness and validator contract

### Client work

- Split content, refresh, and per-lens operation state.
- Make `isNetworkCancellation(error)` a guard at every Briefing operation boundary, including
  manual refresh.
- Preserve stale/cached content through all recoverable errors.
- Replace numeric `response.version >= current.version` race protection with request generations.
  Numeric versions identify server states; they are not a safe substitute for task ownership,
  especially after a server rebuild with a lower version.
- Accept only responses belonging to the latest applicable request generation.
- Treat a changed server version as global invalidation. Treat `304` as unchanged. A `200` at the
  same version may update a migrated ETag but does not create same-version content semantics.
- For lens responses, require coherence with the accepted global state. A response from another
  version is not installed as fresh; revalidate the index instead.

### Server work

- Generate an opaque per-user ETag for `/api/briefing`.
- Add private/no-cache and authorization-varying response headers.
- Audit all index/lens representation mutations and ensure they bump the user's global version.
- Codify the same-version invariant in router/service tests.

### Primary files

- `client/newsly/newsly/ViewModels/BriefingViewModel.swift`
- `client/newsly/newsly/Services/BriefingService.swift`
- `client/newsly/newsly/Views/Briefing/BriefingView.swift`
- `app/routers/api/briefing.py`
- `app/services/briefing/presentation.py`
- representation-changing Briefing services found by the version-bump audit
- `client/newsly/newslyTests/BriefingViewModelTests.swift`
- `tests/routers/test_api_briefing.py`

## Phase 2 — Single task owner, bounded working set, and snapshot save

### One lifecycle signal

`ContentView` supplies one active/inactive signal derived from:

```text
selected tab is Briefing AND scenePhase is active
```

`BriefingViewModel` owns the resulting tasks. Remove the independent `BriefingView.task` load and
the unstructured `handleTabEntered()` task. Re-entering while already active is a no-op.

View-model-owned keyed work includes:

- activation/index revalidation;
- manual refresh request;
- refresh version poll;
- foreground selected-lens load;
- bounded background prefetch;
- read-mark flush;
- snapshot save.

Manual refresh cancels activation revalidation and background prefetch before proceeding. A second
manual-refresh action joins or no-ops rather than issuing another POST. Backgrounding cancels
polling and prefetch without error; the next activation revalidates.

### Request budget

- Restore snapshot without networking.
- Revalidate the index exactly once on activation.
- Load the selected lens at foreground priority.
- Prefetch only previous/next entries in `pagerLenses`; fixed one-page tiers have no pager
  neighbors.
- Allow at most two background lens requests concurrently.
- On a global change, retain stale payloads and reload only selected + pager neighbors.
- Cancel obsolete lens work by generation. Do not rely on URLSession cancellation alone, because
  a response may still arrive.

The normal entry budget becomes one index request plus at most three lens requests instead of one
index request plus all 12 lenses.

### Snapshot save

Snapshot save is local disk persistence, not a server mutation. Implement it as follows:

- Scope the store instance and path to the authenticated `userID`, for example
  `Application Support/Briefing/<userID>/snapshot.json`.
- Include `schemaVersion` and `userID` in the encoded envelope and reject mismatches.
- Pass `userID` through `ContentView` and `RootDependencyFactory`; do not use one process-wide
  user-agnostic snapshot singleton.
- Store the index, last server ETag, selected lens key, selected lens payload, available pager
  neighbors, freshness metadata, and `savedAt`.
- Persist only lens entries known coherent with the snapshot's accepted index state. Never write
  entries currently marked stale.
- Keep the 500 ms cancel-and-replace debounce so bursts coalesce.
- Preserve serial, atomic writes so an older save cannot overwrite a newer save.
- Perform file read, JSON decode, encode, and write off the main actor, preferably behind an actor
  or equivalent serialized async store.
- Keep the 48-hour expiry and clear the current user's file on logout.
- Never fetch a lens solely to populate the snapshot.
- Treat snapshot cancellation, corruption, expiry, and I/O errors as cache misses.

### Primary files

- `client/newsly/newsly/ContentView.swift`
- `client/newsly/newsly/ViewModels/TabCoordinatorViewModel.swift`
- `client/newsly/newsly/ViewModels/BriefingViewModel.swift`
- `client/newsly/newsly/Views/Briefing/BriefingView.swift`
- `client/newsly/newsly/Services/BriefingSnapshotStore.swift`
- `client/newsly/newsly/Shared/AppChrome.swift`
- `client/newsly/newslyTests/BriefingViewModelTests.swift`
- `client/newsly/newslyTests/BriefingViewModelRefreshTests.swift`
- `client/newsly/newslyTests/BriefingViewModelTestSupport.swift`
- `client/newsly/newslyTests/BriefingSnapshotStoreTests.swift`

## Phase 3 — Correct manual-refresh semantics

Replace the 350 ms sleep and one forced GET with:

1. Cancel pending read debounce and await one read-mark flush. A flush failure remains queued and
   does not fail refresh.
2. Cancel background prefetch.
3. POST `/api/briefing/refresh` once.
4. Record the returned server version as the polling baseline.
   `enqueued == false` is not automatically a failure: an equivalent deduplicated task may
   already be pending or processing, so the client still polls from the returned baseline.
5. End the blocking UI action once the server accepts the request.
6. Start VM-owned ETag polling for up to 30 seconds. Begin near 750 ms, back off to at most five
   seconds, and add small jitter.
7. Continue on `304` or a body at the baseline version. Update a migrated/stale ETag if needed
   without treating that alone as refresh completion.
8. On a changed global version, apply the index and load selected + neighbors.
9. On deadline, backgrounding, or cancellation, finish neutrally. Activation remains the backstop.
10. On a genuine POST/poll failure, retain content and expose only action-level retry state.

Without a queue task identifier, polling observes the next global Briefing change rather than
proving completion of one exact worker task. That is the accepted scope. If exact job completion
is later required, add a refresh operation ID as a separate API design; do not infer it from a
per-lens revision.

## Phase 4 — Safe production compaction

The release-DB worker path must use a coverage-preserving prepare/compose/persist algorithm:

1. Read active/degraded segments for one lens and choose a deterministic donor set.
2. Freeze donor IDs, statuses, ordered unread source keys, and the user's starting version.
3. Expand the donor set as needed so replacement windows can meet the configured segment target
   when mathematically possible.
4. De-duplicate sources while preserving donor/source recency order; do not sort opaque source
   keys lexically as a presentation order.
5. Partition every donor unread source into windows bounded by the tier's window maximum.
6. Commit and release the database connection.
7. Compose every replacement outside the transaction.
8. If any required replacement fails, persist none of that lens's replacements and leave all
   donors active.
9. Re-open the transaction and verify the frozen donors are still active and unchanged.
10. Persist all successful replacements as one logical unit.
11. Mark donors compacted only after exact unread-source coverage is proven.
12. Bump the user's global version once for the committed compaction batch.

Required coverage assertion for each frozen donor plan:

```text
frozen unread donor source keys
==
source keys in the committed active replacement windows
```

At the edition level, compaction must never subtract an unread source key. New sources may be
appended concurrently, so whole-edition equality is not required. If concurrent reads or donor
changes make the frozen plan obsolete, abort that lens's compaction and retry during a later
sweep. Correctness takes precedence over hitting the segment target in the same run.

### Primary files

- `app/services/briefing/refresh.py`
- `app/services/briefing/compaction.py`
- `app/services/briefing/segments.py`
- `app/services/briefing/window_composition.py`
- `app/pipeline/handlers/briefing_refresh.py`
- `tests/services/briefing/test_refresh.py`
- `tests/services/briefing/test_compaction.py`
- `tests/services/briefing/test_window_composition.py`
- `admin/remote_ops.py` and admin tests for over-cap visibility

## Phase 5 — Conditional hard payload ceiling

Compaction should materially reduce payloads, but it is not a permanent transport bound. After
Phases 1–4 have production measurements, use the following gate:

- If any lens response remains above 300 KB, or selected-lens LTE latency remains above the target,
  add cursor pagination to the lens endpoint.
- Otherwise defer pagination and keep the simpler API.

Candidate contract:

```text
GET /api/briefing/lenses/{key}?limit=12&cursor=<created_at,id>
```

Each page returns segments in the existing stable order and includes source DTOs only for that
page's segments. Roll out optional parameters first for compatibility; after supported clients are
deployed, make the bounded behavior the default. Pagination does not change the global-version
model.

## Diagnostics and operational visibility

Add structured, content-free diagnostics for:

- activation source and active/inactive transitions;
- operation ID, task key, request generation, and cancellation boundary;
- index request duration, status (`200`/`304`), prior/new version, and ETag-changed flag;
- lens key, response byte count, duration, version, cache state, and foreground/prefetch priority;
- refresh POST duration, returned baseline version, poll count, completion/deadline outcome;
- snapshot user scope, byte count, lens count, and read/decode/write duration;
- queue wait time, worker runtime, compaction donor/replacement counts, and coverage result.

Extend `admin briefing status --user-id` with at least:

- configured segment cap;
- lenses above the cap;
- maximum and total active segment counts;
- source-reference counts;
- a payload-size estimate or measured serialized bytes where practical.

No titles, prose, source URLs, auth tokens, or snapshot contents belong in these logs.

## Test plan

### iOS regression and state tests

- cancelled manual refresh preserves ready content and produces no full-screen error;
- wrapped `APIError.networkError(URLError(.cancelled))` is also neutral;
- genuine manual-refresh failure with cached content is nonfatal;
- genuine initial failure without a snapshot is fatal;
- snapshot/offline activation retains cached content;
- one active transition produces exactly one index request;
- duplicate active/manual actions do not produce duplicate POSTs;
- entry performs at most selected + two neighbor lens requests;
- maximum background lens concurrency is two;
- fixed-lens pager selection does not prefetch unrelated tiers;
- global version/ETag change marks all cached lenses stale but reloads only the working set;
- stale lens and index responses cannot overwrite a newer request generation;
- a lower server version from the latest authoritative request can replace a pre-rebuild snapshot;
- manual refresh polls past the old 350 ms window and applies a later version;
- poll deadline and app-background cancellation are neutral;
- failed read flush does not block refresh and retains keys for retry;
- partial snapshot restore loads a missing selected lens after `304`;
- snapshot save contains only selected/neighbor fresh entries;
- snapshot debounce writes the newest state last;
- user 1's snapshot cannot load for user 2, including equal numeric versions;
- expired and corrupt snapshots are harmless cache misses;
- snapshot load/decode is not performed on the main actor.

### Backend contract and compaction tests

- same user + unchanged representation honors `304`;
- two users at the same numeric version receive different ETags;
- a cross-user ETag never returns `304`;
- private/no-cache and authorization-varying headers are present;
- every audited representation mutation bumps the per-user global version;
- the release-DB worker path actually invokes compaction;
- compaction never loses unread source keys;
- a failed replacement leaves donors active and creates no duplicate active replacements;
- concurrent donor/read changes abort stale compaction plans safely;
- a large donor set produces multiple bounded replacement windows;
- source recency order is retained;
- final segment count respects the configured target when mathematically possible;
- a single committed compaction batch bumps the version once;
- if Phase 5 is triggered, paged responses are stable, compatible, and below 300 KB.

### Verification commands

Use the focused iOS test target for `BriefingViewModelTests` and new snapshot-store tests, followed
by the broader native iOS suite required by the repo. For backend work:

```bash
uv run pytest tests/routers/test_api_briefing.py -v
uv run pytest tests/services/briefing/test_refresh.py tests/services/briefing/test_compaction.py \
  tests/services/briefing/test_window_composition.py -v
uv run ruff check app/routers/api/briefing.py app/services/briefing/refresh.py \
  app/services/briefing/compaction.py app/services/briefing/segments.py \
  app/services/briefing/window_composition.py \
  app/pipeline/handlers/briefing_refresh.py tests/routers/test_api_briefing.py \
  tests/services/briefing/test_refresh.py tests/services/briefing/test_compaction.py \
  tests/services/briefing/test_window_composition.py
```

Run the existing Briefing Maestro flows after the client phases, including cold launch, offline
snapshot display, category swiping, empty-state refresh, and background/foreground transitions.
For this implementation pass, the equivalent refresh and lifecycle boundary was also exercised
directly with AXe against the local API; Maestro remained unavailable because this machine has no
Java runtime.

## Production acceptance gates

- zero full-screen cancellation errors;
- one index request per activation;
- no more than three initial lens requests;
- no duplicate refresh POST for one user action;
- initial Briefing transfer typically below 750 KB; after the payload gate, the selected-plus-two-
  neighbor working set is capped at about 925 KB including index overhead;
- refresh enqueue p95 below 500 ms;
- selected lens usable within two seconds over LTE after compaction/payload bounding;
- no individual lens response above 300 KB after the Phase 5 gate is resolved;
- snapshot read/decode does not cause a measurable main-thread stall;
- no cross-account snapshot or ETag reuse;
- no unread-source coverage loss before/after compaction;
- no active lens silently growing beyond the configured segment cap without appearing in
  `admin briefing status`.

## Rollout order

1. Land Phase 1 with regression tests first. This removes the user-visible cancellation failure
   without waiting for performance or backend work.
2. Land Phase 2 as a separate reviewable client change. Measure request count, transfer, snapshot
   bytes, and cold-start behavior.
3. Land Phase 3 and verify refresh behavior against a deliberately delayed local queue worker.
4. Land Phase 4 behind focused tests; run a dry-run/coverage report against production-shaped
   local data before enabling compaction in the worker path.
5. Deploy and observe for at least one normal Briefing cycle. Trigger Phase 5 only if the payload
   gate fails.

Each phase should be independently revertible. Do not combine the client task-state refactor and
backend compaction in one commit or pull request.

## Explicit non-goals

- per-lens revisions or ETags;
- client-side segment/source diffing;
- a delta endpoint;
- persisting every lens for offline completeness;
- waiting indefinitely for a queued refresh;
- making the snapshot a second source of truth;
- enabling the current compaction helper unchanged;
- requiring identification of the original cancellation producer before fixing the invariant.

## Definition of done

The initiative is complete when a cancelled, delayed, offline, duplicated, or backgrounded
Briefing operation cannot displace usable content; activation and refresh stay within the bounded
request policy; snapshots are async and per-user; the global per-user version/ETag contract is
enforced; production compaction preserves exact unread-source coverage; and measured payloads pass
the pagination gate.
