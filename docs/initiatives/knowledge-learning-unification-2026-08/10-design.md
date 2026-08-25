# Knowledge + Learning Unification Design

**Date:** 2026-08-25
**Status:** Proposed
**Scope:** iOS two-tab architecture, unified Knowledge timeline, uniform row/tile system,
redesigned bottom bar, chat typography, Postgres FTS knowledge search, and a host-side
`search_knowledge` chat tool.

## Goal

Collapse the Knowledge and Learning tabs into one **Knowledge** surface and make saved
articles, chats, Learning Decks, and narrations read as one coherent product:

- One reverse-chronological timeline that interleaves saved content with learning activity.
- One uniform row anatomy: saved-article thumbnails get smaller, learning glyph tiles get
  larger, and every kind lands on the same leading tile size.
- Chat becomes better at *finding* things: a host-side `search_knowledge` tool backed by
  Postgres full-text search, used preferentially before VM corpus exploration or web search.
- Chat reading typography gets one step smaller and denser.
- The bottom bar becomes a two-item morphing glass pill.

The bottom bar becomes:

```text
Briefing | Knowledge
```

## Recorded Decisions

1. The combined tab is named **Knowledge** and keeps accessibility identifier
   `tab.knowledge`. `RootTab.learning` is removed; every Learning route, deep link, and
   voice-start path retargets Knowledge. Knowledge remains the durable saved library
   (law K1); learning activity is presented inside it, not merged into its data model.
2. The Knowledge root is a single merged timeline of four sources — saved content, chat
   sessions, Learning Decks, narrations — under day delimiters, extending the existing
   `LearningTimelineItem` merge rather than introducing a server-owned aggregate feed.
   Partial-source failure stays visible (law K5 semantics now cover the merged surface).
3. The "Ask anything…" composer moves to the top of the combined screen, directly under the
   masthead, keeping Learning's conversational entry as the primary action.
4. The combined root has **no filter chips or segmented control** — the 2026-07-12
   decision against filter controls carries over. Kind legibility comes from the uniform
   tile content and kicker line, and lookup comes from search. Topics/collections/AI
   clustering remain out of scope.
5. Every row uses one **56×56** leading tile (continuous corner radius 10). Saved articles
   shrink from 92×76 thumbnails; learning glyph tiles grow from 38×38 with glyphs at 22pt
   (up from 16pt). Kind is communicated by the tile content plus an uppercase kicker line,
   never by row shape.
6. Rows share one text anatomy: Lora 18 title (≤2 lines), optional Lato 12 subtitle
   (1 line), and a Lato 9 uppercase kicker line `KIND · SOURCE/STATUS · TIME`. The
   per-item date column under Learning tiles is removed; day delimiters plus the kicker
   time carry recency.
7. The bottom bar is rebuilt as a two-item glass capsule with a sliding selection pill
   (matched-geometry), icon-only unselected items, and an icon+label expanded selected
   item. Existing glass/shadow/motion tokens are authoritative; no new design-system
   primitives.
8. Chat message reading type steps down from 16pt to 15pt via a new `Font.chatBody`
   role consumed by both the user bubble and the assistant markdown renderer. Composer,
   timestamps, and pills keep their current sizes.
9. `search_knowledge` is a **host-side** tool, like `web_search`: it runs Postgres FTS on
   the API host and injects results into model context. It performs no sandbox operation,
   so a turn that only searches knowledge stays VM-free (law CH12) and does not join the
   five-tool VM surface (law CH14).
10. The knowledge FTS query and its GIN index are defined from one shared SQL expression
    builder (the proven `news_search_expressions` pattern), fixing the existing
    content-search index/query mismatch as part of this work.
11. The same FTS path serves the chat tool, the assistant router's knowledge route, and
    the Knowledge tab's search field, so search quality cannot drift between surfaces.
12. Existing colors, fonts, spacing scale, corner radii, image pipeline, and motion tokens
    are authoritative. New tokens are limited to `Font.chatBody` and the tile constant.

## Knowledge Screen

### Structure

```text
EditorialMastheadHeader("Knowledge")            search · more

[ Ask anything...                                       mic ]

---------------------- TODAY ----------------------
[56 thumb]  How Solar Got Cheap                      ›
            The state of the transition, in one …
            SAVED · CONSTRUCTION PHYSICS · 2H AGO

[56 glyph]  Battery chemistry follow-up              ›
            You asked about LFP cathode costs
            CHAT · 4:12 PM

[56 deck]   Grid-Scale Storage                       ›
            8 cards · ready to review
            DECK · UPDATED TODAY

[56 wave]   Morning long-read narration              ▶
            12 min
            AUDIO · READY

-------------------- YESTERDAY --------------------
...

                 Briefing | Knowledge
```

- Root remains a plain scrolling feed (`List` + `.appListRow()` per the current
  `LearningView`) with stable identity, `EditorialMastheadHeader` first, and the existing
  top/bottom screen edge fades.
- Masthead actions keep the Knowledge search icon (pushes `KnowledgeSearchRoute`) and the
  more menu; both stay `.appSymbol(size: 19–20, weight: .semibold)` in 44pt targets.
- The composer is the existing Learning composer unchanged: `terracottaBodyLarge` field,
  48pt min height, `CornerRadius.control` surface, send/mic trailing controls, and the
  same durable send path into `ChatSessionRoute`.

### Timeline model

`LearningTimelineItem` generalizes to `KnowledgeTimelineItem` with a fourth case:

```text
saved(ContentSummary)      activity = savedAt (fallback createdAt)
chat(ChatSessionSummary)   activity = lastActivityDate
deck(LearningDeck)         activity = updatedAt ?? latestRun.updatedAt ?? createdAt
narration(AudioEpisode)    activity = updatedAt ?? createdAt
```

- Namespaced stable IDs (`saved-<contentId>`, `chat-<sessionId>`, …) so polling and
  pagination never collide across kinds.
- Initial load fans out the four existing requests concurrently; any subset may fail
  without erasing the others. Pull-to-refresh reloads all four.
- Pagination: saved content and chats keep their existing cursors/page sizes; the scroll
  trigger requests more from whichever paginated source's oldest loaded item is newest,
  so the merged stream never shows a gap. Decks and narrations remain small full lists.
- Deck polling, narration playback ownership, and chat activity polling stay in their
  current view models; the merged root composes them.

### Row anatomy (all kinds)

```text
| 20 | [ 56×56 tile ] | 12 | title (Lora 18 sb, ≤2 lines)          | accessory | 20 |
|    |                |    | subtitle (Lato 12, 1 line, optional)  |           |    |
|    |                |    | KIND · SOURCE/STATUS · TIME (Lato 9)  |           |    |
```

- Row padding: horizontal 20, vertical 10. Divider inset: leading `20 + 56 + 12 = 88`.
- Tile: 56×56, `RoundedRectangle(cornerRadius: 10, style: .continuous)`,
  `surfaceSecondary` base, 0.5pt `outlineVariant.opacity(0.45)` hairline. Images fill and
  clip via `CachedAsyncImage`; glyphs render `.appSymbol(size: 22, weight: .regular)` in
  `onSurfaceSecondary`. The 5pt `PreparingActivityDot` overlay carries busy state.
- Title `.terracottaHeadlineSmall`; subtitle `.terracottaBodySmall` in `onSurfaceSecondary`;
  kicker line via `.kicker(color: .onSurfaceTertiary)` joined with `·` separators.
- Default accessory: chevron `.appSymbol(size: 11, weight: .semibold)` in
  `onSurfaceTertiary`. Narrations keep the 30×30 play/pause/retry circle.

Per-kind mapping:

| Kind | Tile | Subtitle | Kicker | Accessory |
|---|---|---|---|---|
| Saved | generated thumbnail; `photo` placeholder / progress while processing | summary snippet (1 line) | `SAVED · <source> · <time>`; processing/unavailable status replaces source | chevron; info glyph when unavailable |
| Chat | linked-article thumbnail when present, else `bubble.left.and.bubble.right` glyph | last-activity teaser (existing subtitle) | `CHAT · <time>` | chevron |
| Deck | source thumbnail when already available, else `rectangle.on.rectangle` glyph | `deck.timelineSubtitle` (card count / status) | `DECK · <status> · <time>` | chevron |
| Narration | `waveform` glyph | duration | `AUDIO · <status>` | play circle |

Saved rows keep their current semantics: swipe/menu remove-from-Knowledge, processing rows
non-navigable, unavailable rows removable (laws K1–K3). Chat and deck rows keep
swipe-to-delete and the deck context menu.

### States

- **Loading:** skeleton rows using the 56×56 tile slot.
- **Empty (all sources):** one `EmptyStateView` — icon `books.vertical`, title
  "Your knowledge starts here", copy covering save/share *and* ask-anything.
- **Per-source failure:** the existing inline error affordance above the timeline names the
  failed source and offers retry; loaded sources keep rendering.
- **Search:** `KnowledgeSearchView` is retained as the saved-only search route, rebuilt on
  the shared row component (56px tile variant) and now served by FTS (below).

## Bottom Bar

Two items let the bar shrink and gain personality while keeping the existing glass system:

```text
        ╭──────────────────────────────╮
        │  ▣ Briefing        ◎         │      selected pill slides + morphs
        ╰──────────────────────────────╯
```

- Container: same `GlassEffectContainer` + `.glassEffect(.regular, in: .capsule)` over the
  opaque `surfaceSecondary` capsule (pre-26 fallback unchanged), inner padding 5,
  `HStack(spacing: 6)`, `maxWidth` reduced 264 → **200**, `minHeight` 52 preserved so the
  `persistentBottomChromeInset` measurement contract is untouched.
- **Selected item:** horizontal pill — `HStack(spacing: 6)` of icon
  `.appSymbol(size: 18, weight: .semibold)` and label `.appCaption2.weight(.semibold)` —
  padding 14×9, `Capsule().fill(Color.onSurface)`, `Color.surfacePrimary` foreground.
- **Unselected item:** icon only, `.appSymbol(size: 18, weight: .semibold)`,
  `onSurfaceSecondary`, 44×44 target.
- The selection pill moves between items with `matchedGeometryEffect(id: "tab.pill")` and
  the label fades/slides in, animated with the existing `AppMotion` emphasized spring via
  `respectingReduceMotion` (reduce-motion falls back to a crossfade).
- Light impact haptic on tab change; `CompactTabButtonStyle` press-scale 0.96 retained.
- Icons: Briefing `newspaper`, Knowledge `books.vertical.fill`.
- Unselected items keep full `accessibilityLabel`s despite hiding text; ids stay
  `tab.briefing` / `tab.knowledge`. The bar still hides when a navigation path is non-empty.
- Stock `UITabBar` appearance config for the hidden bar is unchanged.

## Chat Typography

One deliberate step smaller for reading surfaces; input stays as is.

| Surface | Today | New |
|---|---|---|
| User bubble text (`MessageBubble`) | `.appCallout` — Lato 16 | `Font.chatBody` — Lato **15**, relative `.callout` |
| Assistant markdown (`SelectableMarkdownView` base font) | `appSans(size: 16)` | `appSans(size: 15)` (code spans scale to ~12.8) |
| Assistant markdown theme (`Theme.chat`) | em-based | unchanged — inherits the smaller base |
| Timestamps / process pill / retry | 11 / 12 | unchanged |
| Composer field (`ChatComposerDock`) | Lato 14 | unchanged |
| Root composer ("Ask anything…") | Lato 16 | unchanged |

`Font.chatBody` is added to `DesignTokens` as the single chat reading-size owner so both
renderers cannot drift. `ContentTextSize` user scaling continues to apply on top.

## Search Architecture

### Today's gap

`app/services/knowledge_search.py` (the assistant's knowledge tool) is `ILIKE '%q%'` over
title/source/url/metadata with no ranking, and the Knowledge tab's `q` filter is three
`ILIKE`s that never touch body text. Meanwhile real FTS machinery exists in
`app/repositories/search_repository.py` — but its content document expression does not
match the GIN index built by migration `20260409_02`, so content `@@` filters seq-scan.

### One knowledge search document

Add a shared expression builder (pattern: `app/repositories/news_search_expressions.py`)
defining the canonical weighted document over `contents`:

```text
A  content_metadata -> 'summary' ->> 'title'
B  contents.title
C  contents.source
D  contents.search_text
```

- Query side: `websearch_to_tsquery('english', q)` + `ts_rank_cd`, OR'd with `pg_trgm`
  similarity on title/source (`greatest(fts_rank, trgm_rank * 0.25)`), matching the news
  implementation.
- Index side: a new migration drops `idx_contents_search_document_gin` and recreates it
  from the *same* builder, restoring index use for both knowledge search and the existing
  `/api/content/search` path. Trigram indexes are already in place.
- Snippets: `ts_headline` over `search_text` (bounded words, `**` sel markers) so tool
  results carry highlighted context, with a title/summary fallback when body text is empty.
- SQLite (unit-test) fallback keeps a generic LIKE branch, as today; ranking behavior is
  asserted only under the Postgres harness.

### `search_knowledge` service

Rewrite `app/services/knowledge_search.py::search_knowledge` on the shared document:

- Scope: join `content_knowledge_saves` on the authenticated user — knowledge saves only
  (law K1). No silent recent-saves fallback: zero hits returns an explicit empty result so
  the model can escalate deliberately instead of hallucinating relevance.
- Returns ranked hits: `content_id`, title, source, `saved_at`, snippet, and — when the
  user's agent-data ledger has one — the item's VM corpus path (`/data/knowledge/…`) so a
  VM-capable follow-up can `read_file` the full document directly.
- Default limit 8, bounded query length, parameterized throughout.

### Tool exposure

Host-side only — no `AGENT_VM_TOOL_NAMES` membership, no `AgentToolPolicy` flag, no
sandbox acquisition. Laws CH12/CH14 hold: a knowledge-search turn spins up nothing.

- **Article chat agent** (`chat_agent.py`): new `@agent.tool search_knowledge(query,
  limit=8)` registered beside `exa_web_search`, opening a short-lived DB session via a
  session factory on `ChatDeps` (never the request session). Result is a formatted
  markdown block (numbered hits with snippet + corpus path), i.e. an ordinary
  `ToolReturnPart` in model context.
- **Assistant router** (`assistant_router.py`): the existing `search_knowledge` tool body
  switches to the FTS service; routing profiles keep the name registered, and the tool
  joins `ASSISTANT_DEFAULT_TOOL_NAMES` so ad-hoc "find that thing I saved" turns can reach
  it without a dedicated route.
- **Preferential use:** system prompts (chat `SYSTEM_PROMPT_TEXT` and the VM instruction
  block ordering) direct the model to call `search_knowledge` *first* whenever the user is
  looking for something they saved, read, or discussed before; VM tools over `/data` are
  for when snippets are insufficient (use the returned corpus path), and `web_search` is
  for the open web — mirroring CH11's restraint.
- **Budget:** the added schema lands inside `CHAT_TOOL_SCHEMA_RESERVE_TOKENS`; verify the
  reserve still covers the full schema set.
- Agent instances are cached per (feature, model, key): the tool appears after process
  restart, which deploy already provides.

### Knowledge tab search

`GET /api/content/knowledge/list?q=` (`get_knowledge_library_entries`) switches its filter
to the same document expression with rank ordering while keeping the
`ContentListResponse` contract and cursor pagination — the app's search screen and the
LLM tool return the same results for the same query.

## Navigation and State Ownership

- Delete `RootTab.learning`; `TabCoordinatorViewModel` exposes `.briefing` / `.knowledge`.
- Knowledge owns the former Learning `NavigationPath`, chat routes,
  `SessionHistoryRoute`, and voice-start; `ChatNavigationCoordinator` and external/pending
  chat deep links select Knowledge. Legacy `tab.learning` deep-link names map to Knowledge.
- The merged root composes the existing `ContentListViewModel` (saved feed + search),
  `LearningHubViewModel` (chats, composer, voice), `LearningDecksViewModel`, and
  `CustomNarrationLibraryViewModel`, all root-owned `@State` from the dependency factory.
- The old `KnowledgeView` feed body and `LearningView` root are deleted once the merged
  root ships — no parallel legacy paths.

## API Boundary

No new endpoints and no contract regeneration:

- Timeline sources reuse `GET /api/content/knowledge/list`,
  `GET /api/content/chat/sessions/list` (already carries article thumbnails),
  `GET /api/learning/decks`, and the custom-narrations list.
- FTS changes are server-internal (`q` semantics improve; response shapes are unchanged).
- `search_knowledge` is a model-facing tool, not a public API.

## Accessibility

- `knowledge.screen`, `knowledge.search`,
  `knowledge.saved.<id>`, `knowledge.chat.<id>`, `knowledge.deck.<id>`,
  `knowledge.narration.<id>`, `knowledge.chat.input`, `knowledge.chat.mic`.
- Rows combine children into one label ("Saved article, How Solar Got Cheap,
  Construction Physics, two hours ago"), keep 44pt targets, and hide decorative tiles.
- The icon-only unselected tab keeps its spoken label; selection state is exposed via
  traits.

## Laws Impact (on ship)

- `knowledge-and-learning.md`: K5 restates that saved items and learning activity share
  one reverse-chronological Knowledge stream with per-source failure isolation; K1–K4 and
  K6–K11 are unchanged in substance.
- `chat.md`: add — "Knowledge lookup in chat is served by a host-side search tool over the
  user's saved library; searching knowledge alone never acquires a sandbox." CH12–CH15
  otherwise unchanged (`search_knowledge` is host-side like web search).
- Laws are edited in the shipping change, not by this proposal.

## Non-Goals

- A server-owned aggregate timeline endpoint.
- Topics, collections, semantic/vector search, or AI clustering.
- Streaming chat, new chat-agent behavior beyond the tool and prompt ordering.
- New color/shadow/radius/motion systems or theme switching.
- Changes to deck generation, narration generation, or the VM five-tool surface.
- Commit/deploy/TestFlight mechanics beyond this repo's normal flow.
