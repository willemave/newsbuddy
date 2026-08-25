# Knowledge + Learning Unification — Implementation Plan

**Date:** 2026-08-25
**Status:** Proposed
**Design:** [10-design.md](10-design.md)

Each phase is an end-to-end working slice: the affected product path builds, runs, and is
verifiable before the next phase starts. Backend phases (1–2) and iOS phases (3–6) can
proceed in parallel; Phase 7 depends on both.

## Phase 1 — Knowledge FTS foundation (backend)

**Goal:** one canonical FTS document for `contents`, an aligned GIN index, and an
FTS-backed knowledge search service. User-visible result: the Knowledge tab search field
matches body text with ranked results.

1. Add `app/repositories/content_search_expressions.py` exposing
   `content_search_document_expression()` (weights A summary-title / B title / C source /
   D `search_text`) plus the shared rank helper (`websearch_to_tsquery` + `ts_rank_cd`
   OR trigram, combined via `greatest(fts, trgm * 0.25)`), mirroring
   `news_search_expressions.py`.
2. Refactor `_apply_postgres_content_search` in
   `app/repositories/search_repository.py` to consume the builder (behavior-preserving —
   the expression it already uses becomes the canonical one).
3. New Alembic migration: drop `idx_contents_search_document_gin` (from `20260409_02`)
   and recreate it from the same builder's SQL so the index matches the query expression.
   Verify index use with `EXPLAIN` against the Postgres harness.
4. Rewrite `app/services/knowledge_search.py::search_knowledge`:
   - Join `content_knowledge_saves` on `user_id`; rank with the shared helper; order
     rank desc, `saved_at` desc.
   - Add `ts_headline` snippet over `search_text` (bounded words, `**` markers), falling
     back to the stored summary when body text is empty.
   - Return structured hits (`content_id`, `title`, `source`, `saved_at`, `snippet`,
     optional `corpus_path` resolved from `agent_data_files`); drop the recent-saves
     fallback — empty means empty. Keep a generic LIKE branch for SQLite.
   - Raise `MAX_KNOWLEDGE_HITS` to 8 with a bounded query length.
5. Switch `get_knowledge_library_entries(search_query=…)` in
   `app/repositories/content_card_repository.py` to filter/rank via the builder
   (Postgres) while keeping cursor pagination and the `ContentListResponse` contract.

**Tests / validation**

- `tests/repositories/test_search_backend.py`: content ranking still passes; add an
  index-alignment test (document expression == migration SQL, the news-side pattern).
- `tests/services/test_knowledge_search.py`: user scoping, ranking, snippet presence,
  empty-result behavior (no fallback), SQLite branch.
- `tests/migrations/`: revision test for the new migration.
- `ruff check` on touched files; focused `pytest` including the Postgres harness suite.

## Phase 2 — `search_knowledge` chat tool (backend)

**Goal:** chat can find saved knowledge without a VM, and prefers to.

1. Article chat agent (`app/services/chat_agent.py`):
   - Add a session-factory field to `ChatDeps` (populated in `_build_chat_deps`).
   - Register `@agent.tool search_knowledge(ctx, query, limit=8)` beside
     `exa_web_search`; open a short-lived session via the factory, call the Phase 1
     service, format hits as numbered markdown (title — source · saved date ·
     `content_id`, snippet line, corpus path line when present), and log via the
     existing tool-progress path so the client's process summary covers it.
2. Prompt ordering: extend `SYSTEM_PROMPT_TEXT` (and the VM-instructions framing) —
   when the user is looking for something they saved/read/discussed, call
   `search_knowledge` first; use VM file tools on the returned corpus path only when
   snippets are insufficient; reserve `web_search` for the open web.
3. Assistant router (`app/services/assistant_router.py` +
   `app/services/assistant_turn_routing.py`): point the existing knowledge tool at the
   new service (with snippets in `_format_knowledge_hits`), and add its name to
   `ASSISTANT_DEFAULT_TOOL_NAMES` so default-route turns can use it.
4. Confirm `CHAT_TOOL_SCHEMA_RESERVE_TOKENS` covers the added schema
   (`app/services/chat_context_budget.py`); bump the reserve if the measured schema set
   exceeds it.

**Tests / validation**

- `tests/services/test_chat_agent.py`: tool registered; VM-less turn exposes
  `search_knowledge` (and `_prepare_chat_tools` still strips only VM tools); a
  tool-invoking turn performs no sandbox acquisition (CH12 guard).
- `tests/services/test_assistant_router.py` / routing tests: default profile includes
  the tool; knowledge route unchanged.
- `tests/services/test_agent_toolset.py` five-tool VM-surface assertion untouched (the
  tool is host-side).
- Manual: seeded user, real chat turn "find what I saved about X" → tool call in the
  persisted message list with FTS-ranked results.

## Phase 3 — Unified timeline inside Knowledge (iOS)

**Goal:** the Knowledge tab shows the merged four-source timeline with the uniform row
system. Learning tab still exists and unchanged (deleted in Phase 4), so the app stays
shippable mid-flight.

1. Generalize `Models/LearningTimelineItem.swift` → `KnowledgeTimelineItem` with the
   `.saved(ContentSummary)` case, namespaced IDs, and activity-date rules from the design.
2. Build the shared row per the design's anatomy table:
   - `KnowledgeTimelineRow` (56×56 tile, radius 10, glyphs at `.appSymbol(size: 22)`,
     Lora 18 title ≤2 lines, Lato 12 subtitle, kicker line, divider inset 88).
   - Kind renderers adapted from `KnowledgeSavedRow`, `LearningChatRow`,
     `LearningDeckTimelineRow`, `LearningNarrationRow`; keep the `PreparingActivityDot`,
     narration play circle, swipe/context actions, and saved-state semantics.
3. Rebuild `KnowledgeView` root: masthead → composer (moved from `LearningView`) →
   filter chips → day-grouped timeline; per-source inline errors; skeleton and unified
   empty states; pull-to-refresh across all four sources.
4. Compose `ContentListViewModel`, `LearningHubViewModel`, `LearningDecksViewModel`, and
   `CustomNarrationLibraryViewModel` in the Knowledge root via `RootDependencyFactory`;
   implement the newest-oldest-loaded pagination rule across the two cursor feeds; wire
   chip scoping.
5. Rebuild `KnowledgeSearchView` results on the shared row component.

**Tests / validation**

- Unit: merge ordering across four kinds, stable IDs, day grouping, partial-source
  failure, chip filtering, pagination-source selection.
- Deterministic visual fixtures: saved article (ready/processing/unavailable), linked
  chat, deck, narration in one seeded timeline.
- Focused XCTest + iPhone simulator build; AXe pass over `knowledge.*` identifiers.

## Phase 4 — Two-tab navigation (iOS)

**Goal:** Learning tab removed; Knowledge owns chat.

1. Remove `RootTab.learning`; collapse `TabCoordinatorViewModel`, `ContentView` tab
   wrappers, and `RootTabs` to Briefing + Knowledge; move chat/deck/narration routes and
   `SessionHistoryRoute` onto Knowledge's `NavigationPath`.
2. Retarget `ChatNavigationCoordinator`, external/pending chat sessions, voice-start,
   and Long Read "list narrations" to Knowledge; map legacy `tab.learning` deep-link
   names to Knowledge.
3. Delete `LearningView` and the old Knowledge feed body; remove orphaned components.
   Keep the `persistentBottomChromeInset` measurement path intact.

**Tests / validation**

- Tab-coordinator unit tests: availability, selection, re-tap reset, deep-link mapping
  (including legacy learning links).
- Maestro primary-screen flows updated for two tabs; chat open/send/return flow passes.

## Phase 5 — Bottom bar redesign (iOS)

**Goal:** the two-item morphing glass pill from the design.

1. Rework `CompactTabBar` (`Shared/AppChrome.swift`): `maxWidth` 200, spacing 6,
   selected item as icon+label pill (`Capsule().fill(Color.onSurface)`, 14×9 padding,
   icon 18 semibold, `appCaption2` semibold label), unselected icon-only 44×44,
   `matchedGeometryEffect(id: "tab.pill")` with the `AppMotion` emphasized spring under
   `respectingReduceMotion` (crossfade fallback), light impact haptic on change.
2. Preserve: glass container + opaque capsule base and pre-26 fallback, `minHeight` 52,
   height measurement, hide-on-push behavior, press scale, accessibility labels/traits
   on icon-only items.

**Tests / validation**

- Snapshot/visual fixture for both selection states, light and dark.
- AXe: both tabs labeled and selectable; measured chrome inset unchanged (chat composer
  clearance verified in simulator).
- Reduce-motion run shows crossfade, no slide.

## Phase 6 — Chat typography (iOS)

1. Add `Font.chatBody` (Lato 15, relative `.callout`) to `DesignTokens`.
2. `MessageBubble`: user text `.appCallout` → `.chatBody`; assistant
   `SelectableMarkdownView` base font 16 → 15 (theme is em-based; code spans follow).
3. Leave composer fields, timestamps, pills, and empty states unchanged; verify
   `ContentTextSize` scaling still applies at every step.

**Validation:** chat visual fixture before/after at default and largest text sizes;
simulator read-through of a long markdown answer.

## Phase 7 — Laws, docs, verification sweep

1. Update `docs/laws/knowledge-and-learning.md` (K5 restatement) and `docs/laws/chat.md`
   (host-side knowledge search law) per the design's Laws Impact section.
2. Update `docs/log.md` per phase; final entry records validation evidence.
3. Full sweep: `ruff check` + focused `pytest` (Postgres harness included), iOS build +
   focused XCTest, Maestro flows, AXe on Briefing/Knowledge/chat, seeded screenshots of
   the Knowledge root (all four kinds + chips), search results, chat session, and both
   tab-bar states; screenshot paths recorded in the handoff.

## Risks and Mitigations

- **Index rebuild on `contents`:** GIN recreation locks writes; use
  `CREATE INDEX CONCURRENTLY` semantics in the migration path used for production and
  verify with `EXPLAIN` on the harness.
- **Merged pagination gaps:** the newest-oldest-loaded rule is the invariant to unit-test
  hardest; regressions surface as missing days between sources.
- **Tool schema budget:** measure the serialized schema set after Phase 2; adjust
  `CHAT_TOOL_SCHEMA_RESERVE_TOKENS` rather than letting history trimming absorb it.
- **Agent caching:** new tool/prompt appear only on process restart — irrelevant in
  deploy, easy to trip over in local testing; restart the API when validating.
- **Deep-link compatibility:** legacy `learning` links must land on Knowledge; covered by
  Phase 4 mapping tests.

## Explicit Cut Lines

If scope pressure hits, cut from the bottom: filter chips (Phase 3 step 4's chip scoping)
→ haptic/morph niceties (Phase 5, keep the two-item bar plain) → corpus-path bridging in
tool results (Phase 1 step 4). The FTS foundation, the tool itself, the merged timeline,
and the two-tab navigation are not cuttable.
