# Knowledge and Learning Tabs Design

**Date:** 2026-07-12
**Status:** Implemented and simulator-verified
**Scope:** iOS tab architecture, saved Knowledge presentation, Learning activity timeline,
saved-only search, test fixtures, and simulator verification

## Goal

Replace the mixed Knowledge hub with two focused product surfaces:

- **Knowledge** is the visual library for everything the user saves or shares into Newsly.
- **Learning** is the conversational and active-learning surface containing chats, Learning Decks,
  and created narrations in one chronological timeline.

The Briefing experience uses a compact three-item bottom bar:

```text
Briefing | Knowledge | Learning
```

The same Knowledge and Learning roots remain available in the classic reading experience so chat,
decks, and narrations do not disappear when the reading-experience setting changes.

## Recorded Decisions

1. Knowledge starts with **Recently Saved**. There is no topic area in this iteration.
2. Knowledge has no filter tabs or segmented control beneath the masthead.
3. A small search affordance appears in the Knowledge masthead and searches saved Knowledge only.
4. Every Knowledge row reserves one image position and uses the existing generated thumbnail/full
   image pipeline. Processing content renders an image-shaped loading placeholder in the same slot.
5. Learning follows the chronological timeline direction: chats, decks, and narrations interleave
   under day delimiters.
6. A chat-linked article displays at most one image. Standalone chats use the existing chat glyph.
7. A Learning Deck displays a compact feature-local preview assembled from existing surfaces and
   typography. It may use a source thumbnail when one is already available, but does not introduce
   a new image-generation pipeline.
8. Narrations reuse the existing waveform, duration, playback, processing, and failure vocabulary.
9. Knowledge and Learning use `EditorialMastheadHeader` as the first scroll child. The search
   accessory overlays the Knowledge masthead, so it cannot shift the title baseline or header
   height relative to Learning.
10. Existing colors, fonts, spacing, corner radii, row surfaces, motion, image loading, and compact
    tab chrome are authoritative. New design-system tokens are out of scope unless implementation
    proves an existing role cannot express a required state.
11. Topics, collections, AI clustering, and cross-topic organization are explicitly deferred.

## Knowledge Screen

### Structure

```text
EditorialMastheadHeader("Knowledge")                  search icon

RECENTLY SAVED
[image] title
        source · relative time
[image] title
        source · relative time
...

Briefing | Knowledge | Learning
```

The root uses a plain scrolling feed with stable content identity and pagination. It does not wrap
each row in a large dashboard card. Rows use one thumbnail, an editorial title, compact source/time
metadata, and the existing saved-state and processing semantics.

The masthead search button pushes a dedicated saved-Knowledge search route. Search results use the
same visual row component, preventing search and browse from drifting into separate designs.

### States

- **Initial loading:** existing loading vocabulary with image-shaped skeleton rows.
- **Empty:** the existing saved-library empty message, updated only if necessary to reflect share
  and save behavior.
- **Processing:** fixed image slot plus progress copy; the row is not navigable until ready.
- **Unavailable:** fixed image slot plus existing destructive status treatment; removal remains
  available.
- **Pagination:** the existing scroll-depth pagination modifier and cursor contract.
- **Search:** debounced saved-only server query; empty query does not make a request.

## Learning Screen

### Structure

```text
EditorialMastheadHeader("Learning")

[ Ask anything...                                      mic ]

CONTINUE
---------------- TODAY ----------------
[one image] chat title
            CHAT · last activity

[deck preview] deck title
               DECK · status

[waveform] narration title                       play
           NARRATION · duration/status

-------------- YESTERDAY --------------
...

Briefing | Knowledge | Learning
```

The root composer creates a chat turn and pushes the resulting `ChatSessionRoute` on the Learning
tab's navigation stack. The full chat screen retains its bottom composer. Existing chat deep links,
history navigation, and voice-start routes select Learning instead of Knowledge.

### Timeline model

The client owns a stable `LearningTimelineItem` enum:

```text
chat(ChatSessionSummary)
deck(LearningDeck)
narration(AudioEpisode)
```

Each case supplies a namespaced stable ID, activity date, display metadata, and route/action. Items
sort descending by meaningful activity:

- chat: `lastActivityDate`
- deck: `updatedAt ?? latestRun.updatedAt ?? createdAt`
- narration: `updatedAt ?? createdAt`

The initial load runs the three existing requests concurrently. Partial success remains visible; a
failure in one source does not erase successful rows from the other two. Pull-to-refresh reloads all
three. Existing deck polling and narration playback ownership remain in their current view models.

## Navigation and State Ownership

- Add `RootTab.learning` with a stable log name and accessibility identifier `tab.learning`.
- Give Learning its own `NavigationPath`; Knowledge and Learning never share route history.
- Move chat-route ownership and `SessionHistoryRoute` presentation to Learning.
- Route external/pending chat sessions to Learning.
- Route the Long Read "list narrations" affordance to Learning and scroll to the first narration.
- Keep feature view models root-owned with `@State` and pass observable objects explicitly.
- Preserve the compact bottom-bar height measurement so chat composers remain above three-item
  chrome exactly as they currently remain above two-item chrome.

## API Boundary

### Saved search

Extend `GET /api/content/knowledge/list` with an optional bounded `q` parameter. The response remains
`ContentListResponse`, so no new result DTO is required. The repository applies the query only to
the authenticated user's Knowledge rows and keeps cursor pagination.

### Chat thumbnail

Add optional `article_image_url` and `article_thumbnail_url` to `ChatSessionSummaryDto`, resolved in
the existing chat read-model query from the linked content. The Learning timeline consumes one
thumbnail at most and does not perform one content-detail request per row.

Any public-contract changes must update the registry/OpenAPI fixtures and regenerate checked-in iOS
contracts through the repository scripts; generated Swift is never edited by hand.

## Component Boundaries

Feature-local additions:

- `KnowledgeSavedRow`: one-image saved-content row shared by browse and saved search.
- `KnowledgeSearchView`: saved-only search route.
- `LearningView`: root composer, errors, day groups, and refresh lifecycle.
- `LearningTimelineItem`: stable merged presentation model.
- `LearningTimelineRow`: case router only.
- `LearningChatRow`, `LearningDeckTimelineRow`, `LearningNarrationRow`: focused item renderers.
- `LearningDeckPreview`: compact local preview built from existing tokens.

Existing components to reuse:

- `EditorialMastheadHeader`
- `CachedAsyncImage` and `ServerImageURL`
- `TapToTalkMicButton`
- `NarrationPlaybackControlRow`
- `ContentZoomTransition`
- `PaginationScrollTrigger`
- `Spacing`, `CornerRadius`, `AppMotion`, and semantic color/font roles

The old Knowledge dashboard sections become removable once their chat, deck, and narration entry
points are represented in Learning. Avoid parallel legacy and new product paths.

## Accessibility

- `knowledge.screen`, `knowledge.search`, and `knowledge.saved.<content-id>` identify the saved surface.
- `learning.screen`, `learning.chat.input`, `learning.chat.mic`, and
  `learning.<chat|deck|narration>.<id>` identify
  the Learning surface.
- Every visual preview is hidden from accessibility when its containing row already exposes the
  title, kind, status, and action.
- Rows combine their children into one meaningful label/value and keep a 44-point interactive
  target.
- AXe verification uses selector taps, followed by a fresh `describe-ui` or screenshot after every
  navigation-changing action.

## Tests and Verification

1. Unit-test tab availability, selection, retap behavior, and Learning deep-link routing.
2. Unit-test timeline merge ordering, stable IDs, day grouping, and partial-source failure.
3. Unit-test saved search scoping and cursor behavior on the backend.
4. Unit-test chat-summary image resolution and generated contract fixtures.
5. Add deterministic iOS visual fixtures containing an illustrated saved article, a linked chat,
   a completed deck, and a completed narration.
6. Update Maestro primary-screen coverage for the new Knowledge and Learning roots and the three-tab
   Briefing chrome.
7. Run focused Python tests and `ruff` on changed backend files.
8. Run focused XCTest, then build and launch on an iPhone 17 Pro simulator.
9. Use AXe to inspect the accessibility tree, switch Knowledge → Learning by identifier, open search,
   return, and verify at least one row per Learning kind.
10. Capture final Knowledge and Learning screenshots with seeded test data and include their local
    paths in the handoff.

## Rollout Order

1. Add and test the backend saved-search and chat-thumbnail projections.
2. Add the Learning timeline model and focused rows with unit fixtures.
3. Replace the Knowledge dashboard with the visual saved feed and saved-only search.
4. Add the Learning tab, path, routes, and three-item compact chrome.
5. Move chat/narration entry points and delete obsolete Knowledge-dashboard presentation code.
6. Seed deterministic visual data, update E2E flows, build, AXe-verify, and capture screenshots.

## Non-Goals

- Topic shelves, collections, or AI clustering in Knowledge
- A combined Knowledge/Learning top switcher
- A server-owned aggregate Learning timeline endpoint
- New color, type, shadow, radius, or animation systems
- Changes to Learning Deck generation, narration generation, or chat-agent behavior
- Commit, push, deploy, or TestFlight release

## Implementation Verification

- Backend saved-search and chat-thumbnail tests: 6 passed.
- Focused iOS contract and tab-coordinator tests: 34 passed.
- iPhone 17 Pro simulator build: passed.
- Seeded Knowledge/Learning visual regression: passed.
- AXe confirmed all three compact tabs, Recently Saved rows, interleaved chat/deck/narration rows,
  and the saved-only search empty state.
