# Architecture Improvement Plan — 2026-06-09

This plan is based on the 2026-06-09 multi-agent architecture review of the Python backend and the SwiftUI iOS app (9 review dimensions, 71 findings, each finding cited to file:line). It is the successor to `docs/architecture-improvement-plan-2026-04-27.md` and follows the same conventions: `docs/architecture.md` stays the canonical architecture reference; this document is an execution plan.

Scope is the four workstreams requested: **quick wins**, **deleting unused generator code**, **performance**, and **layering**. The review's pipeline-reliability cluster (retry semantics, watchdog leases, poison-task handling) and the broader type-safety overhaul are intentionally out of scope here; see "Out of scope / follow-ups" at the end.

Confidence labels: items marked **[verified]** were adversarially re-checked against the code during or after the review; items marked **[confirm-first]** came out of the review with strong cited evidence but were not independently re-verified — their first implementation step is a 10-minute confirmation of the claim.

## Implementation status

Workstream A — Quick wins

- [x] A1. Add a contract-freshness gate to CI (after B1)
- [x] A2. Validate pagination cursors with a Pydantic model
- [x] A3. Add missing feed/read-status indexes
- [x] A4. Serialize API datetimes with an explicit UTC designator globally
- [x] A5. iOS badge pollers: equality guards, real logging, stop on logout
- [x] A6. Share one httpx client in the HackerNews strategy
- [x] A7. Move presentation builders out of `routers/` into `app/presenters/`

Workstream B — Delete unused generator code

- [x] B1. Delete the dead Swift OpenAPI client and its generation pipeline
- [x] B2. Delete `streamNDJSON` from `APIClient.swift`
- [x] B3. Migrate off and delete the drifted legacy `ContentType`/`ContentStatus` Swift enums
- [x] B4. Delete `toggle_knowledge_save` (test-only caller)

Workstream C — Performance

- [x] C1. Convert no-await endpoints from `async def` to `def`
- [x] C2. Stop holding DB sessions/transactions across LLM calls in chat turns
- [x] C3. Reuse the Chromium browser across article crawl attempts
- [x] C4. Remove the per-claim retry-bucket scan from queue dequeue
- [x] C5. Move audio-episode generation off the HTTP request path
- [x] C6. Split narration playback progress into its own observable
- [x] C7. Isolate `dragAmount` from the ContentDetailView scroll content
- [x] C8. Consolidate the two badge pollers and make cadence adaptive
- [x] C9. Downsample images to display size in `ImageCacheService`
- [x] C10. Move long markdown rendering off the main thread; memoize per-cell parsing
- [x] C11. Confine the chat "thinking" timer invalidation to `ThinkingBubbleView`

Workstream D — Layering

- [x] D1. Extract `routers/api/chat.py` orchestration into commands/queries
- [x] D2. Extract a shared `chat_turn_runtime` from `chat_agent` / `assistant_router`
- [x] D3. Split `services/onboarding.py` into an `onboarding/` package
- [x] D4. Split `services/audio_episodes.py` into an `audio_episodes/` package
- [x] D5. Make `knowledge_repository` pure data access
- [x] D6. Extract discovery subscription rules into commands
- [x] D7. Make `commands/ingest_content.py` the single content-ingestion entrypoint

## Brief readout

Three structural facts drive most of this plan.

First, **the API contract is generated but unenforced**. `scripts/check_public_contracts.sh` regenerates and diffs every public artifact, but its only caller is `scripts/architecture_guard.sh`, which no CI job or hook runs — CI has exactly four jobs (Ruff, Go CLI, Pytest, iOS Tests). The predicted rot has already happened: commit `26084e54` added `key_takeaway` to `ContentSummaryResponse` and `ContentSummary.swift`, but `docs/library/reference/openapi.json` was never regenerated. Compounding this, the largest generated artifact — the 2.1 MB Swift OpenAPI client in `client/newsly/OpenAPI/Generated/` — is dead code: "OpenAPI" appears zero times in `project.pbxproj`, there are zero references to `OpenAPIRuntime` or `Components.Schemas` anywhere in app code, and the generator build is the slow part of the contract check. The hand-rolled Codable models in `client/newsly/newsly/Models/` are the de facto contract. The plan deletes the dead generator (B1) and then makes the cheap remaining checks a CI gate (A1).

Second, **the backend's layer discipline is excellent except in three hot spots**. Twenty of twenty-one API routers follow the documented routers → commands/queries → repositories/services direction; `chat.py` (1,733 lines, 29 direct ORM queries, zero commands/queries imports) and `discovery.py` (10 query sites) are the outliers, and two near-identical 1,500-line chat-turn services duplicate six helpers verbatim. The layering workstream targets exactly those, plus two god modules with clean seams already marked.

Third, **both apps do expensive work on their hottest paths**. The backend runs sync SQLAlchemy inside `async def` handlers on a single-process uvicorn, holds pooled DB sessions open across multi-minute LLM calls, sorts two feed queries on un-indexed expressions, and relaunches Chromium per crawl. The iOS app re-evaluates its heaviest screen (the 3,266-line `ContentDetailView`) per drag frame and twice a second during narration, polls two stats endpoints every 5 seconds forever (even after logout), and decodes full-resolution images for 60 pt thumbnails.

## Ranked recommendations

| Rank | Item | Impact | Effort | Risk |
| ---: | --- | --- | --- | --- |
| 1 | B1 + A1: delete dead Swift client, gate contracts in CI | Very high | S | L |
| 2 | C1: `def`-conversion of sync endpoints (event-loop unblocking) | High | S-M | L |
| 3 | D1: extract `chat.py` router orchestration | High | M-L | M |
| 4 | A3 + A2: feed indexes, cursor validation | High | S | L |
| 5 | C2 + D2: shared chat-turn runtime that releases sessions around LLM calls | High | M | M |
| 6 | C6 + C7 + C8: iOS render-storm fixes (narration, drag, pollers) | High | S-M | L-M |
| 7 | C5 + D4: audio generation off the request path | High | M | M |
| 8 | C9 + C10: image downsampling, markdown rendering costs | Medium-high | M | L |
| 9 | D3 + D5 + D6 + D7: remaining layering extractions | Medium | M | L |
| 10 | A4 + B3: datetime serialization, legacy enum deletion | Medium | S | L-M |

---

## Workstream A — Quick wins

Each item here is independently shippable in under a day. A1 depends on B1 landing first (it makes the contract check fast enough for CI).

### A1. Add a contract-freshness gate to CI **[verified]**

**Problem.** `.github/workflows/ci.yml` has four jobs; none runs `scripts/check_public_contracts.sh` or `scripts/architecture_guard.sh`. The checked-in `docs/library/reference/openapi.json` is already stale on main (commit `26084e54` changed `ContentSummaryResponse` without regenerating).

**Why it matters.** Every silent drift between the Pydantic models and the hand-rolled Swift models currently ships unnoticed; the review traced one to a user-visible bug class (silently blank summary sections). A red PR is the cheapest possible fix.

**Target shape.**

- A `Contracts` step in the existing Pytest job (Python deps are already synced there): run `scripts/export_openapi_schema.py` + diff, `scripts/generate_ios_contracts.py` + diff. Both take seconds once B1 removes the swift-openapi-generator build.
- The Go artifact comparison (`generate_agent_cli_artifacts.sh`) goes in the existing Go CLI job, which already has Go set up.

**First commits.**

1. Run `scripts/regenerate_public_contracts.sh` and commit the artifact diff — this fixes the current staleness and proves the pipeline works.
2. Add the CI steps (split python/go parts of `check_public_contracts.sh` per the target shape above, or restructure the script with `--python-only` / `--go-only` flags so CI and `architecture_guard.sh` share one implementation).

**Validation.** Introduce a deliberate field rename locally; confirm the step fails with a readable diff. Confirm total added CI time is under ~1 minute.

### A2. Validate pagination cursors with a Pydantic model **[verified]**

**Problem.** `PaginationCursor.decode_cursor` (`app/utils/pagination.py:43-67`) returns `dict[str, Any]` and only validates `last_created_at`. `last_id` is never checked for presence or type. Seven call sites index it directly; in `app/routers/api/chat.py` the indexing happens outside the `try` that wraps `decode_cursor`, and in `get_recently_read.py`/`list_content_cards.py` the surrounding `except ValueError` does not catch `KeyError`. A crafted-but-well-formed cursor (valid base64 JSON without `last_id`) returns an unhandled 500 on those endpoints.

**Target shape.**

```python
class CursorData(BaseModel):
    last_id: int
    last_created_at: datetime
    filters_hash: str | None = None
```

`decode_cursor` validates into this model and maps `ValidationError` to the existing `ValueError` contract, so all call sites keep their 400 path. Call sites switch from `cursor_data["last_id"]` to `cursor.last_id`.

**Validation.** New tests in `tests/utils/`: cursor missing `last_id` → `ValueError`; non-int `last_id` → `ValueError`; round-trip with filters hash. One router-level test asserting 400 (not 500) for a malformed cursor on `/api/chat/sessions`.

### A3. Add missing feed/read-status indexes **[verified]**

**Problem.** Two hot list queries sort on un-indexed expressions:

- The recently-read list sorts `content_read_status` by `read_at` per user, but the table only has the unique `(user_id, content_id)` index plus single-column indexes (`app/models/db/content.py:125-136`). It also recomputes `available_dates` on every page.
- The inbox feed orders by `COALESCE(publication_date, processed_at, created_at)` (`app/repositories/content_feed_query.py:15-17`), which no index covers.

**Target shape.** One alembic migration in `migrations/alembic/versions/`:

- `Index("idx_content_read_user_read_at", "user_id", "read_at")` on `content_read_status` (also added to the model's `__table_args__`).
- A Postgres expression index on `contents` matching `content_sort_timestamp_expr()` exactly: `COALESCE(publication_date, processed_at, created_at)` (descending, with `id` as tiebreak if the query orders that way — confirm the exact ORDER BY before writing the migration so the expression matches verbatim; an expression index only helps if the expression is identical).

**Validation.** `EXPLAIN ANALYZE` the two queries locally before/after with a few thousand rows; confirm index scans. CI already runs migrations (`ci.yml:108-109`).

### A4. Serialize API datetimes with an explicit UTC designator globally **[confirm-first]**

**Problem.** The DB convention is naive UTC (`app/models/db/common.py`), and Pydantic serializes naive datetimes without a timezone designator. Only `UserResponse` appends "Z" via a `field_serializer` (`app/models/api/users.py:48`) — its docstring admits the iOS incompatibility. The review traced concrete fallout: `ScraperConfig.parseISODate` on iOS uses `ISO8601DateFormatter` with `.withInternetDateTime`, which requires a zone designator, so scraper-config dates parse as nil for every real response.

**Target shape.** A shared annotated type, e.g. `UTCDateTime = Annotated[datetime, PlainSerializer(_to_utc_z_string, when_used="json")]` in a new `app/models/api/base.py` (or a shared base model), adopted across `app/models/api/`. Remove the one-off serializer in `users.py`.

**First commits.**

1. Confirm the claim: hit a scraper-config endpoint locally, check the wire format, and run the iOS `ScraperConfig` decode against it.
2. Add the shared type and adopt it model-by-model, starting with the models iOS parses dates from.
3. iOS side: extract one `ServerDate.parse(_:)` helper with the documented fallback chain and delete the five per-model copies the review found.

**Validation.** The golden fixtures in `tests/contracts/` will diff (wire-format change is intentional — that is the A1 gate doing its job); update them in the same PR. iOS `newslyTests` decode tests against the new format.

**Risk.** This changes the wire shape for all clients. The Go CLI and admin UI need a quick grep for datetime parsing assumptions before shipping.

### A5. iOS badge pollers: equality guards, real logging, stop on logout **[verified]**

**Problem.** `UnreadCountService` and `ProcessingCountService` are copy-paste twins: 5-second `Timer` loops started on `didBecomeActive`, stopped only on `didEnterBackground`. Verified today: assignments to `@Published` properties are unconditional (every poll invalidates the root `TabView` and all mounted tabs even when counts are unchanged), errors are swallowed with `print(...)`, and neither file references `.authDidLogOut`/`.authenticationRequired` — after logout both keep hitting protected endpoints, and the review traced a 401 → refresh-fail → logout-notification cascade firing every 5 seconds.

**Target shape.** In both services:

- Guard each assignment: `if articleCount != response.article { articleCount = response.article }` (or compare the whole response first).
- Replace `print` with an `os.log Logger` matching `APIClient`'s pattern; consider a consecutive-failure counter that backs off the timer.
- Observe `.authDidLogOut` (and the auth-required notification) → invalidate the timer; restart on successful sign-in. Confirm the exact notification names used by `AuthenticationViewModel` during implementation.

This item is deliberately minimal; structural consolidation of the two services is C8.

**Validation.** Unit-test the equality guard by counting `objectWillChange` emissions. Manual: log out, watch the network log stay quiet.

### A6. Share one httpx client in the HackerNews strategy **[verified]**

**Problem.** `_fetch_comments` (`app/processing_strategies/hackernews_strategy.py:136-168`) gathers up to 30 `_fetch_comment` coroutines, each opening its own `httpx.AsyncClient` for a single GET — 30 connection pools and TLS handshakes to the same host per HN item.

**Target shape.** Create one `AsyncClient` per `extract_data` call, pass it into `_fetch_comment`, bound concurrency with a semaphore (~10).

**Validation.** Existing strategy tests; one new test asserting a single client is constructed (inject a factory or patch the constructor).

### A7. Move presentation builders out of `routers/` into `app/presenters/` **[verified]**

**Problem.** `build_content_summary_response` / `build_content_detail_response` live in `app/routers/api/content_responses.py` but are imported by five `queries/` modules — queries importing upward into routers. The mirror-image `queries/news_item_content_adapter.py` is imported upward by `services/news_feed.py`. The cost is already visible: `app/routers/api/__init__.py` had to be hollowed out to stop these imports from dragging the router graph into worker processes, and an empty `tests/presenters/` directory shows the seam was started but never landed.

**Target shape.** New `app/presenters/` package containing `content_responses.py` and `news_item_content_adapter.py`. Queries and services import downward into presenters; routers import presenters. `content_responses.py` itself only imports models/services/utils (verified), so the move is mechanical.

**First commits.** One commit: `git mv`, update the ~8 import sites, restore any convenience re-exports if other code relied on the old paths.

**Validation.** `ruff check`, full `pytest`, and an import-cycle smoke check (`python -c "import app.main"` plus importing a worker entrypoint without the router graph).

---

## Workstream B — Delete unused generator code

### B1. Delete the dead Swift OpenAPI client and its generation pipeline **[verified]**

**Problem.** `client/newsly/OpenAPI/Generated/` (2.1 MB; `Types.swift` 34,324 lines + `Client.swift` 12,027 lines) is committed, drift-checked, and regenerated on contract changes — but it is compiled into nothing. Verified today: "OpenAPI" appears zero times in `newsly.xcodeproj/project.pbxproj`; there are zero references to `OpenAPIRuntime` or `Components.Schemas` in `newsly/`, `ShareExtension/`, or `newslyTests/`; `Types.swift` imports `OpenAPIRuntime`, which is not a project dependency, so the files could not compile even if added. Meanwhile `scripts/generate_ios_openapi_artifacts.sh` clones and builds swift-openapi-generator — the slow step that keeps the whole contract check out of CI.

**Decision.** Delete rather than adopt. The hand-rolled models are the de facto contract, adopting the generated client would be a large migration with no incremental path, and the artifacts are fully derived — if the team ever wants the generated client, it regenerates from `openapi.json` in one command. Nothing is lost.

**What gets deleted.**

- `client/newsly/OpenAPI/` (entire directory).
- `scripts/generate_ios_openapi_artifacts.sh`.
- The `IOS_OPENAPI_TMP` block in `scripts/check_public_contracts.sh` (the `compare_dir` against `OpenAPI/Generated`).
- The corresponding call in `scripts/regenerate_public_contracts.sh`.

**What stays.** `export_openapi_schema.py` (the spec itself remains authoritative), `generate_ios_contracts.py` (the `APIContracts.generated.swift` enum sync — this *is* compiled and used), and the Go CLI generation.

**Validation.** `check_public_contracts.sh` passes and completes in seconds; `grep -ri openapi client/newsly --include="*.swift" -l` returns nothing outside docs; iOS build is unaffected (the files were never in a target).

### B2. Delete `streamNDJSON` **[verified]**

`APIClient.swift:353-460` has zero callers (verified today). As written it also has a producer-task leak (no `continuation.onTermination`) and bypasses the 401-refresh path, so it is a trap for whoever adopts it for chat streaming. Delete it; note in the commit message that a future streaming implementation needs `onTermination` task cancellation and the shared refresh-retry path.

### B3. Migrate off and delete the legacy `ContentType`/`ContentStatus` Swift enums **[confirm-first]**

**Problem.** Two parallel enum families exist on iOS: the generated `APIContentType`/`APIContentStatus` (in `APIContracts.generated.swift`, kept fresh by the contract check) and the pre-generation hand-rolled `Models/ContentType.swift` / `Models/ContentStatus.swift`. The review found the legacy pair has already drifted — missing `insight_report`/`unknown` and `pending`/`awaiting_image` — yet still drives display logic: `detailTypeLabel` uses the legacy enum, so insight-report content falls back to the label "Article". `contentTypeEnum` is referenced in 10 files (verified today).

**Target shape.** Delete both legacy files; move display helpers (`displayName`, label/icon mappings) into extensions on the generated enums; migrate the ~10 `contentTypeEnum` call sites to `apiContentType`.

**First commits.**

1. Confirm the drift claim by diffing the two enum definitions, and confirm every legacy case maps cleanly onto a generated case.
2. Add the extensions; migrate call sites one file per commit if the diff gets large; delete the legacy files last.

**Validation.** iOS tests; manual check that an insight-report item now shows the right type label. Run the Maestro visual baselines since labels can appear in snapshots.

### B4. Delete `toggle_knowledge_save` **[confirm-first]**

The review found `app/repositories/knowledge_repository.py`'s `toggle_knowledge_save` has no production callers — only `tests/routers/test_api_content_pagination.py` and `tests/services/test_knowledge.py` use it. Confirm with grep, then delete it and rewrite those tests against `save_to_knowledge`/`remove_from_knowledge`. Fold this into D5 if both land together.

---

## Workstream C — Performance

### Backend

### C1. Convert no-await endpoints from `async def` to `def` **[verified]**

**Problem.** All 43 route handlers are `async def` but take a synchronous `Session` and run blocking SQLAlchemy directly in the coroutine body — on the event loop. The API runs as a single uvicorn process with no `--workers` flag (`docker/run-api.sh`), so one slow query stalls every concurrent request, including health checks and the iOS app's two 5-second pollers.

**Why `def`-conversion.** FastAPI runs plain `def` endpoints in its threadpool. For handlers that never `await`, the conversion is mechanical and behavior-identical, and it captures most of the win without committing to the async-engine migration.

**Target shape.**

- Audit every endpoint in `app/routers/`: (a) no `await`, no task spawning → convert to `def`; (b) genuinely awaits or calls `asyncio.create_task` (the chat send path dispatches background pipelines; onboarding already uses `run_in_threadpool` correctly) → keep `async def`. The audit list goes in the PR description.
- **Caution:** anything calling `asyncio.create_task` or relying on a running loop must stay `async def` — in the threadpool there is no running loop.
- Ops follow-up (separate decision, not this change): consider `--workers 2` in `docker/run-api.sh` for defense in depth.

**Validation.** Full pytest. A concurrency smoke test: issue a deliberately slow request and a fast one in parallel; confirm the fast one no longer serializes behind the slow one.

### C2. Stop holding DB sessions/transactions across LLM calls in chat turns **[verified]**

**Problem.** `process_message_async` (`chat_agent.py:1234-1427`), `process_assistant_turn_async` (`assistant_router.py:1269-1448`), and `deep_research.py:458-653` hold a pooled session — and its open transaction — across multi-minute LLM calls. With `pool_size=20`, a handful of concurrent deep-research turns plus the API's own traffic can exhaust the pool; the connection also sits "idle in transaction" the whole time.

**Target shape.** Three short transaction windows per turn: (1) load session/history/context, copy what the turn needs into plain data, close the session; (2) run the agent with no session held; (3) reopen a session to persist the result and usage. Detached-instance hazards are the main risk — extract primitive fields before closing rather than passing ORM objects into the agent code.

**Sequencing.** Land D2 (shared `chat_turn_runtime`) first if feasible, so this discipline is implemented once instead of three times. If D2 slips, fix `deep_research.py` standalone — it has the longest hold times.

**Validation.** Existing chat/assistant/deep-research tests; under a synthetic 6-concurrent-turn load, watch `pg_stat_activity` for idle-in-transaction connections (should be ~0 during LLM waits).

### C3. Reuse the Chromium browser across article crawl attempts **[verified]**

**Problem.** `html_strategy.py:1252-1307` launches and tears down a full Chromium instance per crawl attempt — browser startup dominates small-page crawl time and multiplies under retries.

**Target shape.** A lazily created, process-lifetime crawler/browser shared across crawls within a worker, with: a recycle policy (max N crawls or M minutes, whichever first — headless Chromium leaks), crash recovery (relaunch on next use if the browser died), and clean shutdown on worker exit. Confirm the crawl4ai API's supported reuse pattern before implementing; if per-call isolation is required for stealth/fingerprint reasons on specific sites, keep a per-call escape hatch.

**Validation.** Process a batch of 20 articles locally; compare wall-clock per item and peak memory before/after. Watch the worker logs for browser-crash recovery behavior.

### C4. Remove the per-claim retry-bucket scan from queue dequeue **[confirm-first]**

**Problem.** Per the review, each dequeue in `app/services/queue.py:367-399` first runs `SELECT DISTINCT coalesce(retry_count, 0)` over the entire claimable backlog, then loops claim attempts per bucket — so draining N tasks costs N full backlog scans. The claim design itself (FOR UPDATE SKIP LOCKED, LISTEN/NOTIFY, poll backoff) is sound.

**First commits.**

1. Confirm: generate a synthetic backlog of a few thousand pending tasks locally and `EXPLAIN ANALYZE` the distinct query; check git history/tests for why bucket rotation exists (fairness between fresh and retried work) so the fix preserves the intent.
2. Either cache the bucket list for a few seconds per worker, or replace distinct-then-loop with a single claim ordered by `(coalesce(retry_count,0) ASC, task_order ASC)` if the fairness analysis allows.

**Validation.** Existing queue tests (claim ordering, retry scheduling); the synthetic-backlog benchmark before/after.

### C5. Move audio-episode generation off the HTTP request path **[verified]**

**Problem.** `stream_audio_episode_chunks` (`audio_episodes.py:784-1113`) is a sync generator wrapped in `StreamingResponse` (`routers/api/audio_episodes.py:336-338`). Starlette iterates it on the shared threadpool, and inside it the code synchronously runs the script LLM call and the TTS loop — holding a threadpool thread, DB state, and the client connection for the entire multi-minute generation. It even mutates `episode.status` mid-stream: a worker state machine living inside a response body.

**Target shape.** The request path always enqueues (`enqueue_audio_episode_generation` already exists at `audio_episodes.py:472`) and responds by tailing the partial file (`follow_audio_episode_stream_chunks` already implements this). The threadpool thread then only ever does file tailing.

**Risk.** First-chunk latency now depends on the audio queue's worker picking up the task; the dedicated `audio_episode` queue keeps this bounded, but measure time-to-first-audio before/after and consider a priority lane if it regresses noticeably.

**Validation.** Tests for enqueue + follow as the only path (success, generation failure mid-stream, client disconnect/reconnect resumes from the partial file). Manual: start an episode, kill the app, reopen — playback resumes.

### iOS

For all iOS performance items, validate with Instruments (SwiftUI view-body counts and hangs) on a device or simulator before/after, and run the Maestro visual baselines afterward since these touch the heaviest screens.

### C6. Split narration playback progress into its own observable **[confirm-first]**

**Problem.** `NarrationPlaybackService` is a coarse `ObservableObject` whose progress timer assigns `@Published currentTime` every 0.5 s. It is observed by `ContentDetailView` (3,266-line body), `LongFormView`, and `ShortFormView` — and the two feed views never read `currentTime`, only `isSpeaking`/`speakingTarget`. During any playback, all three screens re-evaluate their entire bodies at 2 Hz.

**Target shape.** Either split progress state (`currentTime`/`duration`) into a small child observable consumed only by the playback control row, or migrate the service to `@Observable` (deployment target is iOS 18.5, so property-level tracking is available) and let SwiftUI track per-property access. Also skip the assignment when the rounded value is unchanged.

### C7. Isolate `dragAmount` from the ContentDetailView scroll content **[confirm-first]**

**Problem.** `dragAmount` is `@State` on `ContentDetailView`, written on every `DragGesture.onChanged` tick (up to 120 Hz), and read at the view's top level for `.offset(x:)` — so every drag frame rebuilds the full tree: parallax header, summary sections, discussion sections, expanded transcript.

**Target shape.** Extract everything inside the `ScrollView` into a child struct whose inputs do not include `dragAmount`; the thin wrapper applies `.offset` and owns the swipe-indicator overlays. With stable inputs, the child body stops re-running per frame. This is also the first incision for the eventual `ContentDetailView` decomposition (out of scope here, but cut along lines that survive it).

### C8. Consolidate the two badge pollers and make cadence adaptive **[verified problem; design choice open]**

**Problem.** Beyond A5's hygiene fixes: two singletons fire 2 HTTP requests + several DB COUNT queries every 5 seconds for the entire foreground lifetime, even when `processingCount` is 0 and nothing can change.

**Target shape.** One badge-stats poller. Backend: a single combined endpoint in the existing stats router returning both unread and processing counts (both repositories already share a readonly session). Client: one scheduler both services subscribe to, with adaptive cadence — processing counts poll at 5 s only while `processingCount > 0` or right after a submission; unread counts refresh on lifecycle events and mark-read actions rather than a hard timer. Keep the old endpoints serving during a one-release transition.

**Validation.** Charles/proxy or server logs: request rate drops from ~24/min to near-zero at idle. Badge correctness after submit, after mark-read, after background/foreground.

### C9. Downsample images to display size in `ImageCacheService` **[confirm-first]**

**Problem.** `ImageCacheService` stores `UIImage(data:)` at original resolution and `CachedAsyncImage` has no size parameter. `ContentCard` renders 60×60 pt thumbnails from full-size images when `thumbnailUrl` is nil; a single 4000×3000 hero decodes to ~45 MB against the 50 MB `NSCache` cost limit, evicting everything else.

**Target shape.** Add a target pixel size through `CachedAsyncImage` → `ImageCacheService`; downsample on load with ImageIO (`CGImageSourceCreateThumbnailAtIndex` + `kCGImageSourceThumbnailMaxPixelSize`); cache key becomes URL+size; call `byPreparingForDisplay()` off-main before publishing. Keep original bytes on disk; cache only downsampled bitmaps in memory.

**Validation.** Memory gauge while scrolling an image-heavy feed before/after; confirm no visible quality regression on 3x devices (request 2× the point size).

### C10. Move long markdown rendering off the main thread; memoize per-cell parsing **[confirm-first]**

**Problem.** Expanding Transcript/Full Article runs `MarkdownNSRenderer.render` synchronously in `updateUIView` — per-line regex + cmark parsing plus a single `sizeThatFits` over the whole document — a guaranteed main-thread stall for long transcripts. Table rendering re-parses each cell up to three times. Separately, `NewsItemDetailView` re-runs `AttributedString(markdown:)` per key point on every body evaluation.

**Target shape.** Render the `NSAttributedString` on a background task keyed by the existing `RenderKey`, assign `attributedText` on main, show a lightweight placeholder while rendering; memoize `sanitizeTableCell` per cell. For key points: precompute plain strings once at decode/init; the body does `Text(precomputed[i])` only. If very long transcripts still hitch on layout, chunk into paragraph-level views in a `LazyVStack` as a follow-up.

### C11. Confine the chat "thinking" timer invalidation to `ThinkingBubbleView` **[confirm-first]**

**Problem.** `thinkingElapsedSeconds` ticks once per second on `ChatSessionViewModel` and is passed as a parameter into `ChatMessageList`, re-evaluating the whole list (and every visible `MessageRow`, whose five fresh closures defeat SwiftUI's equality short-circuit) each tick.

**Target shape.** Pass a start `Date` once; `ThinkingBubbleView` owns its own `TimelineView`/timer. The per-second invalidation collapses to the bubble.

---

## Workstream D — Layering

All items follow the repo's documented direction: routers → commands/queries → repositories/services → models. Each is a behavior-preserving refactor validated by `ruff check` + the relevant `pytest` subset, plus the focused additions noted below.

### D1. Extract `routers/api/chat.py` orchestration into commands/queries **[verified — the top finding of the review]**

**Problem.** `chat.py` (1,733 lines) is the worst layering outlier in the repo: 29 direct `db.query`/`select` calls and zero imports from `app.commands`/`app.queries`, while 20 of 21 other routers follow the convention (only `discovery.py` shares the violation, at 10 sites). Specifics, all verified line-by-line: `_build_session_summaries` (492-644) is a 150-line read-model builder with 7 batched queries; `create_session` (879-1034) does model resolution, session-type decision, context snapshot, insert, commit, and a personal-markdown side-effect; `send_message` contains the council-branch business rule (1265-1269) and the 4-way pipeline dispatch (1308-1338); the article title/summary resolution block is triplicated (update_session 1095-1107, get_session 1144-1156, _build_session_summaries 595-607).

**Why it matters.** The dispatch rule is unreachable except over HTTP — and queue callers (`app/services/dig_deeper.py:333-340`, `app/pipeline/sequential_task_processor.py`) already bypass it by calling `process_message_async` directly, a real divergence point. The triplicated resolution logic is drift waiting to happen.

**Target shape.** Following the established per-file pattern:

- `app/queries/list_chat_sessions.py` — absorbs `_build_session_summaries` + `_extract_messages_for_display`.
- `app/queries/get_chat_session.py` / `get_chat_message_status.py` — read paths, sharing one title/summary resolution helper (kills the triplication).
- `app/commands/create_chat_session.py` — model resolution, session-type decision, snapshot, insert, commit, markdown sync.
- `app/commands/send_chat_message.py` — owns the council rule and the 4-way dispatch, callable by both the router and queue paths.

The router keeps auth guards, `HTTPException` mapping, and DTO returns.

**Sequencing.** Three slices, each independently shippable: (1) read paths, (2) `create_session`, (3) `send_message` + dispatch. Note: tests import private helpers (`_format_process_summary_label`, `_extract_messages_for_display`) — move those imports with slice 1.

**Validation.** Existing chat router tests pass unchanged (TestClient-level behavior is the contract); add direct query/command tests per the pattern the 04-27 plan already established ("Add direct query tests for extracted router orchestration").

### D2. Extract a shared `chat_turn_runtime` from `chat_agent` / `assistant_router` **[verified]**

**Problem.** `chat_agent.py` (1,572 lines) and `assistant_router.py` (1,464 lines) each implement the same turn lifecycle with six near-verbatim duplicated helpers (`_require_session_id`, `_require_session_user_id`, `_resolve_session_model`, `_personal_library_unavailable_message`, `_build_agent_cache_key`, `_close_sandbox_session`), two module-global agent caches, and two near-identical sandbox-runtime builders. The seam is already informal: `assistant_router` imports `chat_agent`'s private `_log_chat_usage`. Divergent docstrings show drift has started.

**Target shape.** `app/services/chat_turn_runtime.py` owning: session guards, one agent cache (keyed by model+credential), sandbox session lifecycle, message persistence (create/complete/fail), and usage logging (`_log_chat_usage` promoted to public here). The two services keep only their distinct halves: article-context prompting vs screen-context routing and tools. C2's session-release discipline is implemented once, here.

**Validation.** Existing tests for both services; one new test for the shared cache keying (two models → two cache entries; same model+credential → one).

### D3. Split `services/onboarding.py` into an `onboarding/` package **[verified]**

**Problem.** 2,436 lines spanning five responsibilities consumed by five different surfaces (router, pipeline handler, admin web, a query, a command) — so every consumer transitively loads LLM agent setup and Exa client code even to serialize a status. The seams are clean; the heuristic functions (~lines 1048-1780) are pure.

**Target shape.** `app/services/onboarding/` package: `entrypoints.py` (router-facing), `discovery_run.py` (pipeline-handler-facing `run_discover_enrich`/`run_audio_discovery`), `llm_plans.py` (output models + fallback ladders), `query_heuristics.py` (pure functions — add the cheap unit tests this finally makes possible), `persistence.py` (scraper-config/suggestion/seed writes). Import-move only; no behavior change. Keep `app/services/onboarding/__init__.py` re-exporting the old names for one release if churn is a concern, then drop it.

**Validation.** `ruff`, full onboarding test subset, and a worker-process import check (the handler should no longer pull in admin/router code).

### D4. Split `services/audio_episodes.py` into an `audio_episodes/` package **[verified]**

Same shape as D3 for the 1,764-line module: `creation.py` (the four `create_*` orchestrations), `presentation.py` (`present_audio_episode`), `scripting.py` (LLM script generation + fit-to-limit), `streaming.py` (the chunk stream/follow state machines). Pure mechanical split, done before or together with C5 (which then changes behavior in `streaming.py` only).

### D5. Make `knowledge_repository` pure data access **[verified]**

**Problem.** `app/repositories/knowledge_repository.py` inverts the layer direction (imports `app.services.personal_markdown_library`, performs filesystem writes), owns `db.commit()` (so callers can't compose transactions — `commands/convert_news_to_article.py:94` gets an implicit mid-flow commit), and catches bare `Exception` everywhere, returning soft values: `save_to_knowledge` returns `None` for both "already saved" and "database failure", forcing the command to issue an extra existence query and collapse real errors into a generic 500.

**Target shape.** Repository: queries and row mutations only — no service imports, no commits, raise on failure. `commands/save_to_knowledge.py` / `remove_from_knowledge.py` own the markdown sync (with its degraded-sync try/except), the commit, and error mapping. B4's deletion of `toggle_knowledge_save` rides along.

**Validation.** Existing knowledge tests, rewritten where they asserted the soft-failure returns; a new test that a failed save raises (and maps to a non-200) instead of silently returning None.

### D6. Extract discovery subscription rules into commands **[verified]**

**Problem.** `routers/api/discovery.py` is the only other router with direct ORM access (10 sites). `subscribe_discovery_suggestions` (363-435) hard-codes the scraper-type whitelist, the YouTube watch-URL rejection rule, config defaulting, idempotency handling, and per-item error accumulation ending in a single commit of partially-mutated state; `add_discovery_items` (443-506) bypasses commands entirely; the user-scoped suggestion fetch is copy-pasted four times (370, 450, 521, 550).

**Target shape.** `commands/subscribe_discovery_suggestions.py` and `commands/add_discovery_items.py` owning the rules and the commit; a repository helper for the repeated suggestion fetch. Router keeps auth + DTO mapping. The domain rules (which suggestion types are subscribable, the YouTube rule) become unit-testable without a TestClient.

### D7. Make `commands/ingest_content.py` the single content-ingestion entrypoint **[verified]**

**Problem.** `commands/submit_content.py` is a pass-through to `commands/ingest_content.py`, which thinly wraps `services.content_submission.submit_user_content` — while six call sites skip both and call the service directly (`assistant_router` ×3, `x_integration.py:569`, `learning_deck_sources.py:75`, `routers/api/discovery.py:473`). Ingestion has three competing entrypoints and no choke point for future pre/post steps (quota, dedup policy, audit). The inversion also runs upward: `services/learning_deck_sources.py:10` imports from `app.commands.convert_news_to_article`.

**Target shape.** `ingest_content.py` (it already defines the stable `IngestContentResult`) becomes the sole entrypoint; collapse `submit_content.py` into it; route the six direct callers through it; move the logic `learning_deck_sources` needs out of `commands/convert_news_to_article` into a service so commands are only ever imported by routers.

**Sequencing note.** D6 routes `add_discovery_items` through the same entrypoint — land D7 first or together.

---

## Sequencing summary

1. **B1 → A1** (delete the dead client, then gate contracts in CI). Do this first; it protects every subsequent contract-touching change, including A4.
2. **A2, A3, A5, A6, A7, B2** — independent; land any time.
3. **C1** — independent and high-value; early.
4. **D2 → C2** (shared runtime, then session discipline inside it). **D4 → C5** (split, then behavior change). **D7 → D6**.
5. **D1** in three slices, any time after A7 (presenters move reduces churn in the extracted queries).
6. **iOS C6-C11** — independent of the backend work; C7 before any further `ContentDetailView` surgery; C8 after A5.
7. **A4 + B3** last among the quick wins — both change observable behavior (wire format, labels) and benefit from the A1 gate being live.

## Out of scope / follow-ups

- **Pipeline reliability cluster** (PROCESS_CONTENT failures never retried; watchdog requeuing actively-leased tasks; expired-lease reclaim never incrementing `retry_count`; summarize retry-exhaustion stranding content in PROCESSING). These came out of the review as unverified HIGHs and are production-incident-shaped — they deserve their own verification-first plan rather than a line item here.
- **Type-safety overhaul**: `Mapped[]` migration, task payloads `extra="forbid"`, typed summary unions in `ContentDetailResponse`, typed processing-strategy contracts. The A1 gate makes the contract half of this visible; the rest is a separate initiative.
- **`ContentDetailView` full decomposition** and iOS singleton/DI standardization — C7 makes the first cut; the rest belongs in a dedicated iOS-architecture initiative.
- **iOS chat polling deadline** (60-second cap marking deep-research sessions failed) and the post-logout poller cascade beyond A5's stop-on-logout — chat-stability territory, overlapping `docs/initiatives/ios-chat-stability-2026-04`.
