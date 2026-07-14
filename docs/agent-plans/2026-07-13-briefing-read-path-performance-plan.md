# Briefing read-path and rendering performance plan

Date: 2026-07-13

Status: implemented and verified locally; reviewed with Claude CLI `fable`; production/LTE and
real-device acceptance metrics pending deployment

Related plans:

- `docs/agent-plans/2026-07-11-briefing-refresh-reliability-plan.md`
- `docs/agent-plans/2026-07-13-briefing-lens-scroll-read-mark-plan.md`
- `docs/agent-plans/2026-07-13-briefing-timeline-read-threshold-plan.md`

## Outcome

Make Briefing feel immediately usable when it opens, when a reader selects a Lens, and while the
reader scrolls or marks segments read. Preserve the complete current product behavior: every unread
source remains reachable, the editorial ordering and generated prose are unchanged, cached content
remains visible during revalidation, read marking retains its current midpoint semantics, and
links, figures, discussions, Dig Deeper, narration, snapshots, refresh, and first-run behavior
continue to work.

The primary performance work belongs on the read path:

1. make an unchanged index revalidation genuinely cheap;
2. stop hydrating full segment documents for the index;
3. bound the first Lens response and hydrate the rest outside the visible-content critical path;
4. load the selected Lens before speculative neighbors;
5. reduce SwiftUI invalidation and repeated attributed-text construction;
6. optimize generation separately so avoidable database, HTTP setup, and compaction work does not
   delay newly readable material.

Response caching is deliberately excluded. It would add invalidation and deployment complexity
while leaving the largest current costs—unbounded transfer, JSON decoding, and rendering—intact.

## Implementation record

The implementation landed as one uncommitted local change set on 2026-07-13. It includes the
lightweight index validator, projected index reads, the backward-compatible bounded Lens contract,
self-caused read-version fast-forwarding, selected-first iOS hydration with continuation retry,
stable per-segment render models, passage render/measurement reuse, affected-segment-only
optimistic read updates, production-shaped fixtures and server/iOS diagnostics, generation query
batching, refresh-scoped provider reuse, and one bounded composition executor. No response cache or
new cache dependency was added.

Local verification completed:

- focused backend Briefing, generation, admin, compression, and contract tests: 44 passed in the
  final changed-path run (the earlier broader focused matrix passed 93 tests);
- focused Briefing view-model, snapshot, read-mark, attributed-text, and render-model iOS tests:
  passed after the final stale-revalidation changes;
- complete native iOS unit-test suite: passed on an iPhone 17 Pro simulator;
- complete Go CLI suite: passed;
- Python lint and `git diff --check`: passed;
- backend-wide suite: 2,074 passed and 23 skipped; five unrelated post-summary image-routing tests
  failed in the complete order but all five passed together in isolation;
- the two existing Briefing Maestro flows passed against both the normal simulator build and an
  installed Release build;
- the Release configuration builds successfully for the simulator;
- a post-review, read-only 30-iteration local profile against the largest active Lens
  (`news-ai-society`) measured validator p50/p95 0.48/0.67 ms with 2 queries, changed-index p50/p95
  3.83/4.84 ms with 6 queries, and selected first-page p50/p95 12.54/18.41 ms with 8 queries. The
  first page was 110,735 bytes uncompressed and 31,159 bytes gzipped, below the 300 KB ceiling.

Production p95/LTE and real-device Instruments acceptance measurements remain a rollout gate, not
an unimplemented code path. iOS signposts now cover snapshot decode/write, index and Lens
fetch/decode, hydration, render-model construction, publication-to-first-passage, attributed-text
build/measurement, and optimistic read updates. Two simulator `xctrace` attempts stalled while
saving and produced no valid trace. The paired physical iPhone is reachable, but Developer Mode is
disabled, so Xcode cannot mount developer services or collect an Instruments trace. The
main-thread/hitch and LTE gates must therefore be captured after enabling Developer Mode and
deploying the change before declaring the production targets verified.

## Why this work is justified now

The reliability plan deliberately deferred pagination until production showed either a Lens above
300 KB or selected-Lens LTE latency above the target. That gate has been reached:

- the prior production investigation measured individual Lens responses up to about 365 KB and
  roughly 2.1–17.9 seconds;
- the current read-only production status for user 1 reports 366 active segments and 1,237 source
  references across active Lenses;
- eleven active Lenses are above the configured 12-segment target and the largest has 49 active
  segments;
- active segment blocks plus source-key JSON are estimated at about 1.04 MB before source DTOs,
  discussion metadata, and the response envelope are added.

These figures do not substitute for route-level or device-level profiling. The implementation must
capture a fresh baseline before changing behavior, but it does not need another production cycle to
justify activating bounded Lens reads.

An additional production distinct-source aggregate was intentionally not used as evidence because
the remote inspection path stalled. The generation pipeline should calculate the mathematically
required segment floor directly while it already has the relevant source keys.

## Locked architectural decisions

### Keep one global per-user Briefing version

`briefing_states.version` remains the sole application-level representation version. This plan
does not introduce per-Lens versions, per-Lens ETags, content diffs, or a delta endpoint.

Every index and Lens page returns the same global version. A page response from a version other
than the client's accepted representation is stale unless it belongs to the narrowly classified
self-caused read-mark fast-forward described in Phase 2. Every other version transition continues
through the existing generation-based cancellation and stale-while-revalidate rules.

### Conditional HTTP revalidation is not response caching

The existing private ETag remains the transport validator for the index. Its purpose is to avoid
returning an unchanged body, not to retain serialized Briefing responses in an application cache.

This plan does not add:

- an in-process response LRU;
- Redis or another distributed response cache;
- cached serialized Lens or index JSON;
- a materialized response table;
- stale response serving outside the existing client snapshot behavior.

### Preserve unread coverage

An active-segment target is not permission to retire unread material. Compaction may replace donor
segments only when the replacement preserves their unread source coverage. If a Lens contains more
unique unread sources than can fit inside the configured number and size of composition windows,
the correct result is a higher active-segment floor plus bounded transport—not dropped content.

### Preserve backward compatibility during rollout

The existing no-parameter Lens request continues to return the complete Lens for deployed clients.
New clients opt into bounded pages. Bounded behavior may become the default only after the minimum
supported app version understands the page contract.

### Preserve stale-visible reading sessions

Index or Lens revalidation never removes usable content merely because a request is pending or
fails recoverably. A reader can continue using the accepted document while the next representation
loads. Obsolete page responses cannot overwrite a newer accepted representation.

## Required invariants

1. Every unread source returned by the legacy full Lens endpoint is reachable through the bounded
   page sequence exactly once after source-key deduplication.
2. Concatenating all pages at a fixed global version produces the same ordered segments, source
   content, read flags, and Lens summary as the legacy full response.
3. Segments remain ordered by `created_at DESC, id DESC`, including ties.
4. A page includes source DTOs only for source keys referenced by that page.
5. The Lens summary continues to describe the complete active Lens, not only the current page.
6. An obsolete-version page is discarded and triggers normal revalidation unless it belongs to a
   recorded adjacent read-mark fast-forward with no segment retirement.
7. Appending a page does not reset scroll position.
8. Replacing an accepted Lens with a structurally different ordered segment set retains the
   current scroll/read correctness behavior and starts the replacement document at the top.
9. Read-only styling changes with the same ordered loaded segment IDs do not reset scroll.
10. The selected Lens receives network, decode, render-model, and publication priority over
    neighbor prefetch.
11. Optimistic read styling remains immediate and the existing debounced network flush remains in
    place.
12. Narration continues to use the complete server-side Lens, not the subset downloaded so far.
13. Snapshot failure, partial hydration, or expiry remains a harmless cache miss.
14. No performance optimization changes prompts, model choice, composition semantics, figures,
    citations, discussions, or editorial ordering.
15. Every source projection preserves the existing per-user content/news visibility filters.
16. Every discussion metadata change that alters a returned Briefing payload bumps the same global
    per-user version before an unchanged ETag or page sequence can be accepted.

## Current end-to-end pipeline

### Generation and persistence

```text
content/news completion
  -> app/services/briefing/events.py records a pending source
  -> BRIEFING_REFRESH task is debounced and queued
  -> app/pipeline/handlers/briefing_refresh.py invokes run_briefing_refresh
  -> refresh.py assigns taxonomy and plans append/compaction windows
  -> window_composition.py composes windows through openrouter.py
  -> new segments and coverage-preserving compactions are persisted
  -> fully read/idle material is retired under existing rules
  -> the user's global briefing version is bumped
  -> index and Lens endpoints expose the accepted representation
```

Generation correctly happens outside the long database transaction, but avoidable per-Lens source
queries, per-batch executor construction, and per-call HTTP client construction remain candidates
for improvement. Fresh append composition and back-catalog compaction also share the same refresh
run, so compaction can contribute to time before new material becomes visible.

### Backend read path

```text
GET /api/briefing
  -> get_briefing_index
  -> load active Lenses
  -> hydrate every active BriefingSegment ORM row
  -> collect all active source keys and read state
  -> build Lens summaries and first-run state
  -> only then compare the request ETag

GET /api/briefing/lenses/{key}
  -> load every active/degraded segment for the Lens
  -> collect and deduplicate every source key
  -> load read state and full source DTO inputs
  -> validate all blocks and source DTOs
  -> serialize and gzip the complete Lens
```

The index currently pays its full presentation cost even when it ultimately returns `304`. Its
segment query also hydrates JSON block and narration fields that the index never reads.

The Lens path performs bulk source queries rather than a source-by-source N+1, which is good, but
the work and payload are unbounded. Gzip reduces bytes on the wire; it does not reduce database row
hydration, Pydantic validation, JSON encoding/decoding, or SwiftUI work.

### iOS read and render path

```text
Briefing activation
  -> restore per-user snapshot
  -> conditionally fetch the index
  -> accept one global version
  -> load selected + previous/next working set
  -> decode complete Lens responses
  -> publish Lens dictionaries through BriefingViewModel
  -> derive source/timeline/block presentation data in SwiftUI bodies
  -> build attributed passage text in UIViewRepresentable updates
```

The current client already has several good foundations: one index synchronizer, generation-based
task cancellation, a bounded selected-plus-neighbors working set, per-user snapshots, lazy vertical
stacks, cached/downsampled images, and a separate chrome-collapse observation model.

The remaining costs are concentrated in:

- selected and neighbor Lens requests competing in the same loading phase;
- a legacy `ObservableObject` publishing broad index and Lens dictionaries;
- `source(for:)` scanning cached Lenses;
- optimistic read updates mapping every source and segment in every cached Lens;
- source dictionaries, separator indices, enumerated segment arrays, display blocks, and discussion
  chips being derived again during view reconstruction;
- `BriefingPassageView.updateUIView` constructing a new attributed string before comparing it with
  the current one;
- `sizeThatFits` and exclusion-path layout repeating work for stable content and widths.

The current segment-midpoint read-mark work is the baseline. It is directionally positive because
it replaces many block-level geometry events with one segment-level marker while preserving the
reader-visible threshold.

## Options considered

| Option | Benefit | Limitation | Decision |
|---|---|---|---|
| Early validator lookup and projected index query | Removes most warm-index work with minimal contract risk | Does not reduce Lens payloads | Implement first |
| Cursor pagination with page-local source DTOs | Bounds database hydration, validation, transfer, decode, and initial rendering | Requires page merge and version-race handling | Preferred bounded-read design |
| Segment manifest plus exact-ID chunk hydration | Strongest continuity if compaction changes the active set mid-session | Larger API and client surface than needed initially | Keep as fallback if cursor revalidation proves insufficient |
| Client render optimization without API changes | Improves scrolling and read-mark responsiveness | Cannot remove server, transfer, or decode cost | Implement alongside pagination, not instead of it |
| More aggressive compaction or larger composition windows | Can reduce stored segment count | Can change editorial granularity and cannot guarantee a transport ceiling | Generation follow-up only |
| Response caching | Can avoid repeated server construction for an identical key | Adds invalidation/memory complexity and leaves transfer/decode/render costs | Explicitly reject for this initiative |
| Expiring or dropping old unread sources | Shrinks the document | Violates current behavior and coverage guarantees | Reject |

## Phase 0 — Measurement and production-shaped fixtures

Add content-free timing and size diagnostics before changing the contract.

### Server spans

Record these separately for index and Lens requests:

- validator lookup;
- active Lens lookup;
- segment metadata query;
- segment body query;
- read-state query;
- source projection and discussion hydration;
- DTO construction;
- response serialization;
- uncompressed and compressed byte counts;
- segment count, unique source-key count, and returned source DTO count;
- request type: foreground first page, continuation page, or legacy full response.

The existing HTTP middleware's total response duration and content length remain useful, but they
do not identify which presentation stage dominates.

### iOS signposts

Record:

- snapshot read and decode;
- index network wait and decode;
- selected first-page wait and decode;
- render-model construction;
- Lens publication to first visible passage;
- continuation and neighbor page work;
- attributed-text build and measurement;
- optimistic read-state update and view publication;
- snapshot encoding and write.

Include Lens key, global version, segment/source counts, foreground/background priority, page
number, and response bytes. Do not log titles, prose, URLs, auth headers, or source bodies.

### Required baseline scenarios

Measure in a Release build on a real device where possible:

1. warm activation with a valid snapshot and unchanged ETag;
2. cold activation without a snapshot;
3. selecting the current largest Lens;
4. swiping to a neighbor while its request is still pending;
5. scrolling through at least ten segments;
6. crossing multiple read midpoints and flushing one batch;
7. receiving a new global version while midway through a Lens;
8. restoring a partial snapshot offline and reconnecting.

Extend the existing `scripts/generate_test_data.py` shapes where practical to create a backend
fixture with roughly 50 segments and 200 source references. Do not introduce a parallel seed path
if the existing tool can express the production-shaped case. Use it to check query count, payload
size, and serialization without profiling production repeatedly.

## Phase 1 — Make index revalidation cheap

### 1. Check the validator before building the index

Add a lightweight presentation helper that reads only the user's Briefing state version and the
active first-run identity/revision needed by `_briefing_etag`. Its active-run predicate must exactly
match the predicate used by `get_first_run_progress`; it cannot infer first-run presence from
ready-category keys or a partial source count.

Change the router flow to:

```text
read lightweight validator inputs
  -> compute user-scoped ETag
  -> matching If-None-Match: return 304 immediately
  -> otherwise build and return the full index
```

A matching validator must not query active segments, read marks, or source data. It must also be
read-only: a new user with no `BriefingState` row is treated as version 0 for validator purposes and
the `304` path must not call `ensure_state` or create database state through the readonly session.

### 2. Project only the segment fields the index uses

Replace the full `BriefingSegment` ORM load in `_active_segments` with an explicit projection of:

- `lens_id`;
- `source_keys`;
- `created_at`.

Use value rows or a small internal value type rather than deferred ORM columns, so a later helper
cannot accidentally lazy-load the large fields.

Keep generated-at calculation, segment counts, unique source-key handling, unread counts, Lens
ordering, first-run filtering, masthead fields, and response models unchanged.

### 3. Evaluate aggregation only after projection

If the changed-index path remains above budget, compare:

- the projected Python aggregation;
- a PostgreSQL aggregate over segment counts, maximum creation time, and expanded source keys;
- generation-maintained Lens counters.

Choose the simplest measured option. Do not add a materialized response document.

### Phase 1 tests

- a matching ETag returns `304` without invoking `get_briefing_index`;
- a changed ETag returns the same response as before;
- two users at the same numeric version retain distinct validators;
- first-run identity changes invalidate the validator;
- for each of no Briefing state row, active first run, active first run with zero sources, and
  completed/absent first run, the lightweight helper produces exactly the ETag that the full index
  path would produce;
- the lightweight validator performs no writes and does not create a missing Briefing state row;
- projected and legacy helpers produce identical segment counts, source-key sets, unread counts,
  and generated-at values;
- the index query does not select segment blocks, raw markdown, or narration;
- query count remains constant as Lens count grows.

## Phase 2 — Add a backward-compatible bounded Lens contract

### Request and response shape

Add optional pagination parameters:

```text
GET /api/briefing/lenses/{key}?limit=12&cursor=<opaque-created-at-and-id-anchor>
```

The paged response retains the current fields and adds:

```text
next_cursor: string | null
has_more: boolean
```

Contract rules:

- no `limit` and no `cursor` returns the legacy complete Lens;
- `limit` is bounded server-side and initially capped at 12 segments;
- the cursor is keyset-based on the existing stable `created_at DESC, id DESC` order;
- equal timestamps are ordered and paged by ID without gaps or duplicates;
- cursors are treated as opaque by the client, encode the Lens ID plus the keyset anchor, and are
  rejected when used with another Lens;
- every response includes the current global Briefing version;
- the Lens summary describes the full active Lens;
- `segments` contains only the requested page;
- `sources` contains only the deduplicated source keys referenced by those segments;
- `next_cursor` is null exactly when the page sequence is complete.

### Self-caused read-mark fast-forward

The current read-mark command increments the global version whenever it marks at least one source,
even when no segment is retired. Treating every such adjacent bump as a structural invalidation
would cause scrolling, read flushing, and continuation hydration to repeatedly cancel one another.

Extend `BriefingReadMarkResult` and the additive API response with the already computed retired
segment count:

```text
marked: int
retired: int
version: int
```

Old clients ignore the new field. A new client may fast-forward the accepted representation in
place only when all of these are true:

1. the response belongs to the current request generation and authenticated user;
2. its version is exactly the accepted global version plus one;
3. `retired == 0`;
4. the response corresponds to the client's own pending read-key batch.

In that case the client records the old and new versions as one compatible page generation,
advances its accepted version, keeps document generation unchanged, and reapplies its local read
overlay to any page that arrives. A continuation already issued at the old version may merge only
through this recorded compatibility edge; a continuation at the new version merges normally. No
new request is issued against the old version.

If `retired > 0`, the returned version is non-adjacent, or another operation has already advanced
the accepted representation, use normal stale-visible revalidation. This rule adds no per-Lens
version and does not treat unrelated server changes as locally compatible.

If the additive `retired` field and client handling cannot ship together, the transitional fallback
is to delay the first read flush until selected-Lens continuation hydration completes. That fallback
is not the desired steady state because it delays canonical read persistence on long documents.

### Other version changes between pages

The first accepted page establishes the page sequence's global version. Continuation pages merge
only when their version matches it.

If refresh, compaction, segment retirement, non-adjacent read marking, or enrichment advances the
global version before a continuation returns:

1. discard the continuation response;
2. leave the accepted visible document in place;
3. perform the normal index/Lens revalidation for the newer global representation;
4. replace or retain scroll identity according to the existing ordered-segment structural rule;
5. restart continuation hydration for the accepted representation.

This preserves the global-version model and avoids a second per-Lens revision system while keeping
ordinary read-only scrolling from invalidating its own pagination.

### Backend query shape

For a bounded request:

1. load the active Lens and global state;
2. load lightweight metadata/source keys needed for the full Lens summary;
3. select only the requested segment page's body fields;
4. query read state for the full summary keys and page DTO keys in one bounded operation where
   practical;
5. hydrate source and discussion DTO inputs only for the page keys;
6. validate and serialize only that page.

`sources_for_keys` should gain or use a presentation projection that selects only fields required
by `BriefingSourceDto`. It must not load extracted bodies or other large content/news columns that
the DTO does not expose. The projection must preserve the current `ContentStatusEntry` user join
for content and `build_visible_news_item_filter` scoping for news exactly; column narrowing must not
widen source visibility.

The full Lens summary still makes each continuation perform work proportional to the Lens's
lightweight segment/source-key metadata, even though body and source DTO work are proportional to
the page. Measure that separately. If it remains significant after projection, make the summary
optional on continuation pages and retain the authoritative full-Lens summary from page 1. Do not
add response caching or a materialized summary document.

### Payload ceiling

The first implementation uses 12 segments as the maximum page count and measures serialized page
bytes. The production gate remains no page above 300 KB. If a 12-segment page violates that bound,
reduce the default page size first. Defer byte-aware page cutting unless measured pages still break
the ceiling after that adjustment. Do not solve an oversized page by truncating blocks or source
metadata.

### Phase 2 tests

- concatenated pages equal the legacy full response at a fixed version;
- the first, middle, final, empty, and single-segment pages return correct cursors;
- equal `created_at` timestamps produce no gaps or duplicates;
- invalid, malformed, cross-Lens, and out-of-range cursors fail safely;
- page source DTOs contain every referenced source and no unrelated source;
- projected content/news sources retain the legacy per-user visibility filters;
- Lens segment and unread counts remain full-Lens counts on every page;
- a version change between page requests is visible to the client contract;
- an adjacent self-caused read-mark response with `retired == 0` fast-forwards without restarting
  continuation hydration, including an already in-flight old-version continuation;
- retirement, a non-adjacent version, or an unrelated version advance rejects continuation merge
  and follows normal revalidation;
- a discussion metadata change that alters a Lens payload bumps the global per-user version;
- deployed-client requests without parameters retain identical existing fields; additions are
  limited to backward-compatible optional pagination/read-mark fields;
- narration still reads the complete server-side Lens;
- production-shaped pages stay below the payload ceiling.

## Phase 3 — Make the selected Lens the only initial critical path

### Loading order

Replace simultaneous working-set urgency with explicit priorities:

```text
restore snapshot/index
  -> request selected Lens first page
  -> decode, build render model, publish, and paint it
  -> continue selected Lens hydration in the background
  -> start previous/next first-page prefetch serially or with bounded concurrency
  -> promote a neighbor immediately if the reader swipes toward it
```

No neighbor request should delay selected first-page decode or publication. Background work should
be cancellable when selection, app activity, authentication, or request generation changes. Page
network decoding and render-model construction occur off the main actor before one bounded
publication transaction.

### Preserve working-set and snapshot behavior

The optimization moves full working-set hydration off the initial paint path; it does not turn the
snapshot into authority or prefetch all Lenses.

- Persist the selected and immediate-neighbor pages already hydrated by the bounded workflow.
- Allow a partially hydrated Lens snapshot to restore its available pages. Resume from its stored
  cursor only when index revalidation confirms the snapshot version through `304` or an equal
  version. On any newer version, keep the restored pages stale-visible and restart at page 1 for the
  new representation; never issue a continuation from an old snapshot cursor.
- Never trigger network fan-out merely to make a snapshot complete.
- If the app remains active and idle, continuation hydration may complete the current selected
  Lens so its existing reading depth remains available.
- Snapshot byte and I/O diagnostics remain mandatory.

### Page merge rules

- deduplicate segments by ID while retaining server order;
- deduplicate sources by source key;
- merge only into the matching accepted global version/request generation or the explicitly
  recorded adjacent read-mark compatibility edge;
- ignore duplicate continuation responses;
- make continuation retry page-local rather than replacing the whole Lens with an error;
- keep stale loaded pages visible during recoverable failures;
- expose `hasMore` and continuation state separately from initial Lens state.

### Scroll identity

The current Lens `ScrollView` identity includes the loaded segment-ID array. That is correct for an
authoritative structural replacement but would incorrectly reset scroll every time pagination
appends older segments.

Introduce a client-owned document generation with one explicit decision rule:

- preserve document generation when the merged ordered segment-ID array is unchanged or the
  previously loaded array is a strict prefix of the merged array;
- increment document generation for every other accepted ordered-ID difference and reset the
  replacement document to the top;
- read styling, an obsolete response, or a failed page never changes document generation.

Continuation pages contain older segments under the existing descending order, so a valid append
lands strictly below the loaded viewport. No scroll-anchor compensation should be necessary beyond
preserving the scroll container identity. Port the existing `BriefingLensContentIdentity` tests to
this generation rule rather than weakening those structural regression seams.

This keeps the existing scroll/read-mark correctness invariant without tying identity to the global
version alone.

### Phase 3 tests

- selected first page is requested before neighbor work starts;
- first selected content can publish while continuation and neighbors are pending;
- swiping promotes a pending neighbor without duplicate requests;
- maximum background concurrency stays bounded;
- page append preserves scroll/document identity;
- authoritative structural replacement changes identity;
- same-structure read styling preserves identity;
- the prior array being a strict prefix preserves identity, while insertion, removal, reordering,
  or replacement increments generation;
- mismatched-version and obsolete-generation pages are discarded;
- an adjacent no-retirement read fast-forward preserves loaded pages and document generation;
- continuation failure keeps loaded content visible and retries locally;
- partial snapshots resume only after same-version validation and restart at page 1 after a newer
  version;
- logout/user switching cancels and removes all user-scoped page state.

## Phase 4 — Narrow SwiftUI invalidation and repeated render work

### 1. Split coordinator state from per-Lens presentation state

Keep one owner for activation, global version, navigation, refresh, and narration. Move each Lens's
payload, continuation state, error, and derived render model into a narrow observable page state.

`BriefingLensPageView` and its content subviews should not observe unrelated Lens loads, masthead
changes, narration state, or another Lens's optimistic read update.

This can be implemented incrementally without a broad architecture rewrite:

1. introduce a per-Lens page state;
2. pass narrow values/actions into the page and chrome views;
3. retain `BriefingViewModel` as coordinator until the migration is proven;
4. adopt Observation for the new state instead of adding more `ObservableObject` surface.

### 2. Build a stable render model once per accepted payload/page

Precompute outside SwiftUI `body`:

- `sourcesByKey`;
- source-key-to-segment/page locations;
- timeline separator indices or stamps;
- stable segment iteration data;
- display-block arrangement;
- discussion chips by block;
- segment all-read state.

Rebuild only the affected page/segment when source read state or discussion metadata changes.
Build the source-key-to-segment/page location index once here and use that same index for both
render-model updates and read-mark operations; do not maintain a second divergent lookup structure.

### 3. Make attributed text comparison precede construction

Give each passage render input a fingerprint containing all values that affect output:

- text and block identity;
- source/citation chips;
- read styling or emphasis weight;
- dynamic type and relevant accessibility traits;
- color scheme/theme inputs;
- measured width and figure exclusion dimensions.

In `BriefingPassageView.updateUIView`, compare the fingerprint before invoking
`BriefingAttributedTextBuilder`. Cache measurement by the same fingerprint plus width. Invalidate
correctly on dynamic type, theme, width, citation, and figure changes.

The cached builder result must retain both `attributedText` and `plainText`. `onDigDeeper` currently
uses the builder's plain text, so skipping construction cannot leave the selection callback with
missing or stale passage context.

Do not use a global attributed-string cache. The cache belongs to the view/coordinator lifetime and
is bounded naturally by loaded pages.

### 4. Make optimistic read updates proportional to affected content

Replace the scan-and-map of every cached Lens with direct source-key locations in the accepted
working set. The same index must serve the `markSourcesSeen` unread filter so it also replaces the
current per-key `source(for:)` scan before the optimistic update.

For one debounced batch:

1. update the shared accepted read-key overlay once;
2. update only loaded segments/pages containing those keys;
3. recompute only their all-read state and affected Lens unread summaries;
4. publish one main-actor transaction;
5. retain failed keys for the existing retry path.

The midpoint threshold, immediate grey styling, server request batching, segment retirement, and
stale-visible behavior remain unchanged.

### 5. Keep already-good rendering choices

Do not replace the existing `LazyVStack`, cached/downsampled image pipeline, or isolated
chrome-collapse model without trace evidence. Cache `DateFormatter` and guard stable exclusion-path
updates only after the larger work is measured; they are secondary optimizations.

### Phase 4 tests and profiling

- render fingerprints remain stable for identical inputs and change for every visual input;
- attributed strings are not rebuilt for unrelated ViewModel publications;
- cached attributed results retain the correct plain-text context for Dig Deeper;
- dynamic type, theme, width, citation, and figure changes rebuild correctly;
- optimistic reads affect only indexed segments and preserve Lens unread totals;
- one segment midpoint produces one local read transition and one debounced network batch;
- page append and neighbor publication do not rebuild visible unchanged passages;
- a fixed scroll trace has no main-thread stall above the acceptance threshold;
- accessibility labels, selection, links, Dig Deeper, discussion taps, and figure layout remain
  intact.

## Phase 5 — Optimize generation and publication separately

Generation work must be measured independently from the API and rendering work so a faster read
path is not credited to, or blocked by, LLM latency changes.

### 1. Report the achievable compaction floor

For each active Lens, calculate:

```text
unique unread source count
window source limit for the Lens tier
minimum required segment count = ceil(unique unread sources / window source limit)
excess fragmentation = max(active segments - minimum required segment count, 0)
```

Expose those values in structured refresh diagnostics and `admin briefing status`. Compaction
should target removable fragmentation, not repeatedly attempt an impossible global cap.

This metric is diagnostic. It does not change coverage or retirement behavior.

### 2. Batch planning and source hydration

- Load pending rows for planned active Lenses in one bounded query where practical.
- Collect planned source keys across windows before source hydration.
- Hydrate source presentation inputs once per refresh and partition them in memory.
- Reuse read/source-key sets already calculated during planning instead of querying them per Lens.
- Keep database sessions out of concurrent composition threads.

### 3. Reuse the provider connection pool for one refresh run

`openrouter.py` currently creates an `OpenAI` client and `httpx.Client` per composition call. Create
one refresh-scoped provider client/connection pool, share it only in a documented thread-safe way,
and close it when the refresh run finishes.

Preserve request timeouts, model selection, headers, error classification, usage recording, and
retry semantics. Tests must prove a failed call cannot poison later calls in the same refresh.

### 4. Remove per-batch executor churn without increasing provider pressure

Use one bounded executor or equivalent semaphore for the complete planned composition set instead
of constructing a new executor for each batch. Preserve:

- the configured maximum concurrency;
- stable result association with each planned window;
- the existing `result_by_key` ordered re-association after concurrent completion;
- deterministic persistence order;
- partial failure handling;
- provider rate limits and retry backoff.

Do not simply submit every window at once. The desired result is connection/executor reuse and less
head-of-line waiting, not unbounded concurrency.

### 5. Measure append publication separately from compaction

Record fresh-window composition/persistence time separately from back-catalog compaction. If
compaction materially delays newly generated segments after the preceding optimizations, evaluate a
separate lower-priority compaction task.

Splitting publication is not the default first change because it alters transaction and version
timing. It may proceed only with tests proving:

- fresh append persistence is independently atomic;
- each compaction replacement remains all-or-nothing and coverage preserving;
- retries cannot duplicate append or replacement segments;
- global version bumps accurately represent each committed visible change;
- readers never observe donors retired without their replacements.

### Phase 5 tests

- required segment-floor and excess-fragmentation calculations cover news/category window sizes,
  duplicates, zero keys, and large unread sets;
- planning/source query counts remain bounded as Lens count grows;
- provider client construction and close happen once per refresh run;
- concurrent composition preserves window/result association and persistence order;
- malformed provider output, timeout, retry, and one-window failure behavior remain unchanged;
- compaction coverage tests continue to prove exact unread source preservation;
- prompts, normalized blocks, figures, citations, narration text, and model usage accounting are
  unchanged.

## Performance acceptance gates

Capture the baseline first, then use both absolute and relative gates.

### Backend

- unchanged index revalidation performs only validator queries and returns server-side p95 below
  100 ms;
- changed-index server p95 is below 250 ms on the production-shaped fixture, or at least 70% lower
  than the pre-change baseline if environment overhead prevents that absolute target;
- selected first-page server p95 is below 250 ms excluding network transfer;
- no bounded Lens page exceeds 300 KB uncompressed, with 150 KB as the preferred operating target;
- index and page query counts are constant with respect to individual segments/sources and contain
  no per-source query loop;
- legacy full responses remain available during compatibility rollout but are excluded from normal
  new-client activation.

### iOS

- one index request and one selected first-page request are the only requests allowed before first
  selected content can paint;
- selected first content is usable within one second on the warm test profile and two seconds on
  the existing LTE profile;
- neighbor and continuation work never blocks selected first-page decode/publication;
- adjacent self-caused read flushes with no retirement do not cancel, restart, or redownload the
  selected continuation sequence;
- optimistic read-state update p95 is below 8 ms on the main actor;
- the fixed large-Lens scroll trace produces no main-thread hang above 100 ms and materially lowers
  hitch count from baseline;
- appending a continuation page produces no visible jump or scroll reset;
- snapshot restore does not synchronously decode a complete large working set on the main actor.

### Generation

- one provider HTTP connection pool and one bounded concurrency owner are created per refresh run;
- no regression in source coverage, composition success rate, retry rate, model usage accounting,
  or generated output contracts;
- active-segment health reports distinguish required segment floor from removable fragmentation;
- if append/compaction separation is later enabled, time to committed fresh append improves without
  increasing duplicate segments or visible version inconsistencies.

## Verification matrix

### Backend commands

Run focused tests and lint for touched files:

```bash
uv run pytest tests/routers/test_api_briefing.py -v
uv run pytest tests/services/briefing/test_sources.py -v
uv run pytest tests/services/briefing/test_refresh.py \
  tests/services/briefing/test_compaction.py \
  tests/services/briefing/test_window_composition.py \
  tests/services/briefing/test_openrouter.py -v
uv run ruff check app/routers/api/briefing.py app/models/api/briefing.py \
  app/services/briefing/presentation.py app/services/briefing/sources.py \
  app/services/briefing/read_marks.py \
  app/services/briefing/refresh.py app/services/briefing/window_composition.py \
  app/services/briefing/openrouter.py tests/routers/test_api_briefing.py \
  tests/services/briefing/test_sources.py tests/services/briefing/test_refresh.py \
  tests/services/briefing/test_window_composition.py tests/services/briefing/test_openrouter.py
```

### Native iOS tests

Extend and run:

- `BriefingViewModelTests`
- `BriefingViewModelRefreshTests`
- `BriefingSnapshotStoreTests`
- `BriefingReadMarkingTests`
- `BriefingAttributedTextBuilderTests`
- `BriefingTimelineStampTests`

Then run the complete native iOS suite required by the repository.

### UI and device verification

- run the existing Briefing Maestro flows for cold launch, category swiping, article/podcast
  content, empty-state refresh, and background/foreground transitions;
- reproduce the segment read/return flow from the scroll/read-mark plan;
- verify first-run generation and live progress still render correctly;
- use a Release build plus SwiftUI Instruments and Time Profiler on the production-shaped largest
  Lens;
- test normal, constrained/LTE, offline snapshot, and reconnect paths;
- confirm narration covers the complete Lens after only the first page is visible;
- confirm text selection, source links, discussion chips, figures, pull quotes, Dig Deeper, dynamic
  type, dark mode, and accessibility output.

## Rollout order

Each step must be independently reviewable and revertible.

1. Add diagnostics and the production-shaped fixture without changing behavior.
2. Land early index validation and segment projection.
3. Land the optional backend page contract and additive read-mark `retired` field while existing
   clients continue using full responses and ignore the new read-mark field.
4. Land iOS page decoding/merge, read-version fast-forward, and selected-first scheduling behind
   the new optional contract.
5. Land per-Lens observation/render-model narrowing and attributed-text reuse.
6. Verify production read metrics for at least one normal generation/read cycle.
7. Land low-risk generation query, provider-client, and executor reuse separately.
8. Consider append/compaction task separation only if measured generation timing still requires it.

Do not combine backend pagination, the iOS page-state change, and generation concurrency changes in
one commit or pull request. Each boundary has distinct correctness and rollback risks.

## Primary files

### Backend read contract

- `app/routers/api/briefing.py`
- `app/models/api/briefing.py`
- `app/services/briefing/presentation.py`
- `app/services/briefing/sources.py`
- `app/services/briefing/read_marks.py`
- `app/core/compression.py`
- `app/main.py`
- `tests/routers/test_api_briefing.py`
- `tests/services/briefing/test_sources.py`

### iOS loading and rendering

- `client/newsly/newsly/Services/BriefingService.swift`
- `client/newsly/newsly/Services/BriefingSnapshotStore.swift`
- `client/newsly/newsly/ViewModels/BriefingIndexSynchronizer.swift`
- `client/newsly/newsly/ViewModels/BriefingViewModel.swift`
- `client/newsly/newsly/Views/Briefing/BriefingView.swift`
- `client/newsly/newsly/Views/Briefing/BriefingLensContentViews.swift`
- `client/newsly/newsly/Views/Briefing/BriefingPassageView.swift`
- `client/newsly/newsly/Views/Briefing/BriefingAttributedTextBuilder.swift`
- `client/newsly/newsly/Views/Briefing/BriefingReadMarking.swift`
- corresponding `client/newsly/newslyTests/Briefing*Tests.swift` files

### Generation

- `app/services/briefing/refresh.py`
- `app/services/briefing/compaction.py`
- `app/services/briefing/segments.py`
- `app/services/briefing/window_composition.py`
- `app/services/briefing/openrouter.py`
- `app/pipeline/handlers/briefing_refresh.py`
- focused tests under `tests/services/briefing/`

## Explicit non-goals

- response caching of any kind;
- Redis or another new cache dependency;
- materialized serialized index or Lens responses;
- per-Lens versions or ETags;
- client-side content delta synchronization;
- changing the global per-user version contract;
- dropping, expiring, or hiding unread content to hit a segment or payload target;
- changing prompts, model selection, editorial taxonomy, composition window semantics, or layout
  policy as part of read-path work;
- prefetching every Lens;
- replacing the existing image pipeline without profiling evidence;
- making snapshots authoritative or complete across all Lenses;
- combining read-path and generation-concurrency work into one release.

## Definition of done

This initiative is complete when unchanged index reads avoid the full presentation path, new clients
paint a bounded selected-Lens page before continuation or neighbor work, page hydration preserves
the exact legacy full-Lens representation and scroll/read invariants, visible SwiftUI passages no
longer rebuild for unrelated publications, ordinary no-retirement read flushes do not churn the
continuation sequence, and production/device metrics pass the stated gates.

Generation follow-up is complete when refresh diagnostics distinguish required segment count from
removable fragmentation and measured query/client/executor reuse improves refresh work without any
change to generated output, retries, persistence atomicity, global version semantics, or unread
source coverage.
