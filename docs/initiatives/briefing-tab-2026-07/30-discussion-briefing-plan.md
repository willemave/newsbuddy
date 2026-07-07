# Briefing × Discussions — Plan (2026-07)

Follow-up to [10-implementation-plan.md](10-implementation-plan.md). Read that doc's *Recorded
decisions* (D1–D16) first; every choice below is constrained by D4 (append-only immutable
segments), D15 (cache-stable prompts), and D16 (typed flat contracts).

## Goal

Surface community discussions (Hacker News / Reddit threads already fetched and summarized by the
existing discussion pipeline) inside the Briefing tab:

1. **Discussion content in segments** — a compact "community reaction" element attached to
   briefing segments whose sources have discussions.
2. **Link to the summarized discussion view** — tapping it opens the full structured discussion
   summary (overview, topics, representative comments, links).
3. **Incremental updates** — as the discussion pipeline progressively fetches comments and
   generates/merges summaries, the briefing reflects the latest state without regenerating
   segments.

## What already exists (research findings)

**Discussion pipeline** (fully built, orthogonal to briefing today):

- `news_item_discussions` ([app/models/db/news.py:116](../../../app/models/db/news.py)) — one row
  per representative news item; platform, `comment_count`, raw-comment storage refs, and a
  structured `summary` JSON with status/fingerprint tracking columns.
- Summary shape is `DiscussionSummary`
  ([app/models/metadata/summaries.py:688](../../../app/models/metadata/summaries.py)):
  `overview` (20–900 chars), `topics[1..8]{title, summary, stance}`, `notable_links[..10]`,
  `representative_comments[..6]{author, text, reason}`, `external_discussion_url`, `generated_at`.
- **The pipeline is already incremental**: `FETCH_NEWS_ITEM_DISCUSSION` tasks refresh comments on
  a 1h TTL; `plan_discussion_summary()`
  ([app/services/news_discussion_summaries.py:115](../../../app/services/news_discussion_summaries.py))
  picks `NONE | TRACK_SEEN | TRACK_SUMMARIZED | MERGE | FULL` from comment fingerprints; MERGE
  folds new comments into the existing summary (≤4 times, then FULL). Progress is observable via
  `summary_status`, `summary_generated_at`, `comment_count`.
- Read API already exists and is already in the iOS client:
  `GET /api/news/items/{id}/discussion` ([app/routers/api/news.py:127](../../../app/routers/api/news.py)),
  `APIEndpoints.newsItemDiscussion(id:)`
  ([APIEndpoints.swift:51](../../../client/newsly/newsly/Services/APIEndpoints.swift)), decoded by
  `ContentDiscussion.swift`. `ContentDetailView` already renders a discussion sheet with a Summary
  tab ([ContentDetailView.swift:2120](../../../client/newsly/newsly/Views/ContentDetailView.swift)).

**Briefing invariants that matter here**:

- Segments are immutable after insertion (D4); token economics depend on paying ~1.5–1.8k
  tokens/source exactly once.
- Per-source metadata is *not* baked into blocks: the lens GET assembles `BriefingSourceDto`s at
  read time via `sources_for_keys()` and joins live read flags
  ([app/services/briefing/presentation.py:80-98](../../../app/services/briefing/presentation.py)).
  This side-channel is the natural carrier for anything that changes after composition.
- Freshness signal is `briefing_states.version` → ETag on `GET /briefing`; the client re-checks on
  tab entry/foreground/pull-to-refresh. No polling loop, by design.
- Briefing news sources are cluster representatives only — exactly the rows
  `sync_missing_visible_news_item_discussions()` targets. The two pipelines already agree on
  which items matter.

## Approach comparison (token + processing cost)

| # | Approach | LLM tokens for discussion content | On discussion update | Verdict |
|---|---|---|---|---|
| 1 | Recompose the segment when its discussion changes | ~5–10k per segment per update; ×5 with merges | full window recompose | ✗ destroys D4 economics |
| 2 | LLM writes a "reaction paragraph"; server appends/mutates a block in the segment | ~0.5–1k per update | block mutation + narration invalidation + migration | ✗ pays LLM repeatedly for prose the discussion summarizer already wrote; breaks immutability |
| 3 | **Deterministic read-time enrichment**: lens GET joins `news_item_discussions` and ships a compact discussion payload on `BriefingSourceDto`; client renders it inside the segment | **0** (reuses summaries the discussion pipeline already paid for) | one version bump; next lens fetch reflects it | ✓ chosen core |
| 4 | Compose-time context weave: when a summary already exists at composition, add compact reaction context to the window payload so prose can mention it | ~120–180 input tokens/source, once | none (weave is frozen like all prose) | ✓ optional Phase 3 flavor |

Approach 3 makes requirement 3 nearly free: the discussion pipeline's own FULL/MERGE machinery *is*
the incremental update mechanism; briefing just re-reads the latest summary at GET time. The only
new moving part is a version bump so clients know to re-fetch.

## Decisions

1. **BD1 — Discussion content is deterministic and read-time, never stored in blocks.** The lens
   response enriches `BriefingSourceDto` with a compact `discussion` payload derived from
   `news_item_discussions`. Segments stay immutable; zero briefing LLM tokens; always the latest
   summary state with no staleness window beyond client refresh.
2. **BD2 — Client renders a "community reaction" strip inside the segment card** (after the last
   block, one strip per segment listing its discussion-bearing sources; richest first by
   `comment_count`). No new `BriefingBlockType`, no block-contract change, no repair/normalize
   changes.
3. **BD3 — Tap opens a dedicated discussion sheet (modal), not a scroll-into-long-read.**
   Rationale: news items have no long-read view to scroll into; `ArticleReaderView` has no anchor
   mechanism; the modal reuses the existing `GET /api/news/items/{id}/discussion` endpoint and the
   existing summary rendering from `ContentDetailView`. `content:*` sources keep their existing
   path (`ContentDetailView` → discussion sheet), which comes along free when the source sheet
   opens.
4. **BD4 — Incrementality = version bump on summary transitions.** When a news-item discussion
   summary completes or materially updates (FULL or successful MERGE), bump
   `briefing_states.version` for briefing-enabled users who have that item in an *active* segment.
   Existing client version checks (tab entry / foreground / pull-to-refresh) pick it up. No new
   polling, no push, no task.
5. **BD5 — v1 scope is `news:*` sources only.** That's where the modern discussion pipeline lives
   (`content_discussions` is legacy). Longform/podcast sources are unaffected.
6. **BD6 — Two strip states.** `summary_status != "completed"`: platform + comment count only
   ("214 comments on Hacker News"). `completed`: adds a trimmed overview line (≤280 chars,
   sentence-boundary truncation) and one representative comment. The user visibly sees the strip
   upgrade as the pipeline progresses — requirement 3 made legible.
7. **BD7 — The strip payload is compact; the sheet fetches the full summary lazily** from the
   existing discussion endpoint. Keeps lens payloads small (≤ ~400 bytes per discussion source).
8. **BD8 — Narration is untouched.** The strip is excluded from `narration_text` (which is frozen
   on the segment anyway). Phase-3 woven prose narrates naturally like any other prose.

## Contract changes

`app/models/api/briefing.py` (+ enums untouched; regenerate via
`scripts/regenerate_public_contracts.sh`, verify `scripts/check_public_contracts.sh`):

```
BriefingDiscussionDto {
  platform: str                      # "hackernews" | "reddit"
  comment_count: int | None
  summary_status: str                # "not_ready" | "completed" | "failed"
  overview: str | None               # trimmed ≤280 chars, only when completed
  top_comment_author: str | None     # first representative comment, optional
  top_comment_text: str | None
  external_url: str | None           # discussion thread URL
  updated_at: datetime | None        # summary_generated_at, else last_comments_fetched_at
}

BriefingSourceDto += discussion: BriefingDiscussionDto | None = None
```

Flat optional fields per D16. No block/run contract changes.

## Backend changes

### 1. Read-time enrichment (`sources.py` / `presentation.py`)

In `sources_for_keys()` ([app/services/briefing/sources.py](../../../app/services/briefing/sources.py)):
after resolving news rows, one extra query
`SELECT ... FROM news_item_discussions WHERE news_item_id IN (:ids)` (unique-indexed on
`news_item_id`, ≤40 ids per lens) and attach `BriefingDiscussionDto` to each news source's
`dto()`. Skip rows with `last_refresh_status IN ("gone", "unsupported")` or zero
`comment_count` and no summary. Overview trimming is a small pure helper (sentence-boundary cut at
280 chars) with unit tests.

### 2. Version bump hook (`news_item_discussions.py` → briefing service)

New helper in `app/services/briefing/read_marks.py` (or a small `signals.py`):

```
bump_briefing_version_for_news_item(db, news_item_id) -> bool
```

- Guard: `settings.briefing_enabled_user_ids` non-empty (it's tiny; default `[1]`).
- Membership check: active/degraded `briefing_segments` rows for enabled users whose
  `source_keys` JSONB contains `"news:{id}"` (per-user active segment counts are ≤ ~12/lens, so a
  containment filter scoped to enabled users is cheap; no new index needed).
- Bump `briefing_states.version` per matching user (same inline pattern as
  [read_marks.py:55-58](../../../app/services/briefing/read_marks.py)).

Call site: `refresh_news_item_discussion()`
([app/services/news_item_discussions.py:1071-1106](../../../app/services/news_item_discussions.py)),
after `summary_status = "completed"` is set — i.e. only on FULL/MERGE executions, **not** on
comment-count-only refreshes or TRACK_* plans (avoids hourly bump noise; count-only strips arrive
opportunistically with the next natural lens fetch). This mirrors the existing dependency
direction: content/news lifecycle hooks already call into `app.services.briefing`
(`enqueue_ready_source`).

Edge cases:
- Summary lands *before* the item is composed into a segment (item still pending): no bump needed;
  the read-time join delivers it whenever the segment first renders.
- MERGE→FULL fallback and bounded merge counts mean ≤ ~5 bumps per discussion lifetime — no
  coalescing needed.

### 3. Settings

- `briefing_discussion_strip_enabled: bool = True` (kill switch, response-level).
- `briefing_discussion_overview_max_chars: int = 280`.

### 4. (Phase 3, optional) Compose-time weave

When assembling news window payloads (`list_unread_news_sources()` → `briefing_context`), if the
item's discussion summary is already `completed`, append a `Community reaction:` block (overview +
up to 2 topic stances, capped ~600 chars). One `#news_rules` addition in
`app/prompts/briefing/layout.md`: *may* weave one sentence of community reaction into the passage;
never invent reactions; no new markers. Because it's payload-suffix content, D15 cache behavior is
unchanged; because there are no new markers or run kinds, the repair surface (Appendix A
guardrails) is untouched. Flag: `briefing_discussion_context_enabled` (default False until
observed). Compaction windows benefit automatically — by compaction time most summaries exist.

## iOS changes

- **Contracts**: regen via `client/newsly/scripts/regenerate_api_contracts.sh` →
  `APIBriefingSource.discussion`.
- **Strip**: `BriefingDiscussionStrip` view rendered by `BriefingSegmentView`
  ([BriefingView.swift:393](../../../client/newsly/newsly/Views/Briefing/BriefingView.swift))
  after the last block, for the segment's discussion-bearing sources (richest first, "+N more"
  overflow). Two states per BD6. Styling: hairline-topped caption row, near-monochrome per color
  doctrine; platform glyph + count; overview line in `.appCaption`.
- **Sheet**: `BriefingDiscussionSheet` following the `BriefingDigSheet` pattern
  (`.presentationDetents([.fraction(0.75), .large])`). Fetches
  `newsItemDiscussion(id:)` on appear, renders via the summary UI extracted from
  `ContentDetailView.discussionSummaryContent()`
  ([ContentDetailView.swift:2494](../../../client/newsly/newsly/Views/ContentDetailView.swift))
  into a shared component (refactor, not duplication); "Open thread" → `SafariView` with
  `external_url`. Also add a "View discussion" row to the existing news compact source sheet when
  `discussion != nil`.
- **Refresh**: no new mechanism. Verify (and cover with a test) that when the index version
  changes on tab entry/foreground, already-loaded lens pages re-fetch so strips appear/upgrade;
  wire the version comparison in `BriefingViewModel` if it currently only reloads the index.
- **Read marks unaffected**: the strip is not a source link; it contributes nothing to
  seen-tracking.

## End-to-end incremental flow

1. News item reaches READY → briefing pending → composed into a segment (~15–45 min). Discussion
   row may exist with only aggregator `comment_count` → lens GET already ships a count-only strip.
2. Discussion scraper enqueues `FETCH_NEWS_ITEM_DISCUSSION`; comments fetched; summary FULL runs →
   `summary_status="completed"` → **version bump** → user's next tab entry/foreground/pull sees a
   new version → lens re-fetch → strip upgrades to overview + comment.
3. Thread keeps growing → hourly refresh → MERGE updates summary → bump → strip/sheet reflect the
   merged summary on next fetch. After ≤4 merges, FULL regenerates — same flow.
4. Segment retires (all read / news age-out) → strip leaves with it. Read/retirement semantics
   unchanged.

## Cost summary

- **Briefing LLM tokens: 0** for the core feature (summaries are paid for once by the existing
  discussion pipeline regardless of briefing).
- **Per lens GET**: +1 indexed `IN` query over ≤40 ids; +≤400 bytes per discussion source.
- **Per summary generation**: +1 scoped membership query + version bump for ≤len(enabled_users).
- **Phase 3 weave**: ≤~4k input tokens/day at 40 news sources/day with partial summary coverage;
  output +1 sentence per affected passage. Cache-friendly (suffix position).

## Phases

### Phase 1 — Backend enrichment + version bump (S/M)

- [ ] `BriefingDiscussionDto` + `BriefingSourceDto.discussion`; regenerate + check contracts.
- [ ] Discussion join in `sources_for_keys()`; overview trimming helper; settings knobs.
- [ ] `bump_briefing_version_for_news_item()` + call site in `refresh_news_item_discussion()`.
- Tests: enrichment join (completed / not_ready / gone / no-row cases), trimming edges, bump hook
  (bumps only for enabled users with active-segment membership; no bump on TRACK_*/fetch-only
  paths), lens GET snapshot with discussion payload.
- Acceptance: local lens response for a user with a summarized discussion shows the payload;
  completing a summary bumps the version (assert via `GET /briefing` ETag change).

### Phase 2 — iOS strip + sheet (M)

- [ ] Regenerate Swift contracts; `BriefingDiscussionStrip`; `BriefingDiscussionSheet` (+ shared
      summary-rendering component extracted from `ContentDetailView`); source-sheet row.
- [ ] Version-change → lens re-fetch verification/wiring in `BriefingViewModel`.
- Tests: strip state rendering (count-only vs summarized), sheet fetch + render from a fixture
  `ContentDiscussionResponse`, view-model version-refresh test.
- Acceptance: simulator run — segment shows count-only strip, then after the summary lands and
  version bumps, re-entering the tab upgrades the strip; tapping opens the sheet with the full
  summarized discussion; "Open thread" works.

### Phase 3 — Compose-time weave (S, optional, flag-gated)

- [ ] `briefing_context` reaction block; `#news_rules` addendum; flag default False.
- Tests: payload construction includes/excludes context by summary state and flag; prompt snapshot.
- Acceptance: with the flag on, a locally composed window over discussion-bearing sources yields
  prose referencing community reaction without new markers or repair warnings; token deltas
  visible in `vendor_usage_records`.

## Risks & open questions

- **Client refresh cadence**: strips update only on natural version checks (no polling, per app
  doctrine). If that feels too slow in practice, a bounded option is one delayed re-poll after
  tab entry when visible sources have `summary_status != "completed"` — explicitly out of v1.
- **Payload growth**: 40 sources × 400 bytes ≈ 16KB worst case per lens — acceptable; revisit if
  representative comments prove unnecessary in the strip.
- **`content:*` discussions**: legacy path stays reachable via `ContentDetailView`; if longform
  discussions get migrated to the modern pipeline later, BD1's read-time join extends naturally.
- **Bump fan-out**: fine while `briefing_enabled_user_ids` is small. If briefing goes
  multi-tenant, replace the per-summary membership query with a batched sweep-time bump.
