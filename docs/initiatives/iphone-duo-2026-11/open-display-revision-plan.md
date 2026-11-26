# Open Display Revision — Plan (2026-11-26)

**Status:** Implemented 2026-11-26 on `feature/iphone-duo` (uncommitted); see `docs/log.md`. Open items: hinge rule for detail action buttons (awaiting user), interstitial after the selected item leaves the list. It builds on the uncommitted `feature/iphone-duo` work (see `plan.md`, including its "Spread revision" and "Phase 0 findings" sections).
**Scope:** iOS open-display layout (regular × regular) only, except where noted. Compact phones and the closed Duo keep today's layout, with one exception: chat history adopts the shared row style everywhere (decision R5).

## What the user asked for

1. **News** keeps the newspaper text spread, with a Settings choice between one and two columns.
2. **Podcasts and Articles** use a list on the left page and the selected item's detail on the right page. Before anything is picked, the right page shows a friendly interstitial instead of a blank pane.
3. **Chats** look like the iPhone Knowledge list, so Knowledge, Chats, Podcasts and Articles all read as one family of lists.
4. Every capability these screens have today stays.


## Revision (2026-11-26, after reviewing the final-screen mocks)

These supersede the matching parts of R0, R3, R5, R6 and Phase C below.

- **One Knowledge list.** The rail is News, Podcasts, Articles | Knowledge, plus the player. There is no separate Chats destination.
  - The phone's Knowledge list already holds saved items, chats and decks in one stream (K5). The open display uses that same list in the sidebar, and selected chats open in the right pane.
  - The Chats interstitial is dropped.
  - Phase C shrinks to restyling the chat History screen (reached from a chat's History button) with Knowledge rows, so it matches the rest.
- **Narrow sidebar lists.** Podcasts, Articles and Knowledge use a fixed 300 pt sidebar (x 53–353, about 122 pt before the hinge). The detail pane takes the rest (x 353–867).
  - The detail content reflows to the wider pane, verified on the Duo simulator.
  - News is unchanged: its single reflowed column still ends 40 pt before the hinge.
- **Hinge rule for the wider pane.** The pane now crosses the hinge. Text and images flow over it, but the detail's action-button row must not put a button on the hinge line. In the simulator capture, the third button landed on x≈475. The action row inserts its gap at the hinge, using the reserved region when available.
- **No overlay for Articles, Podcasts or Knowledge.**
  - The detail pane sits flush against the sidebar, separated by a plain hairline: no shadow, no rounded corner, no slide-in, no close button.
  - Picking another row replaces it. The interstitial shows only before the first pick, or after the selected item leaves the list.
  - Overlay-style presentation (`OpenDisplaySlideOverPanel`) is deleted entirely. News, too, opens side by side (R2), and its trailing page keeps a ✕ that restores the full spread.
- **No right gutter: the status bar and camera float over content.**
  - The open shell ignores the trailing container safe area, so every page runs to the screen edge.
  - The system status bar (and camera, when active) float transparently on top, as at the top of a phone.
  - Nothing is padded to avoid them. Titles, chips and text may run under the status icons all the way to the right edge, by design. There are no clock-avoiding modifiers.
  - The Briefing detail pane drops its `NavigationStack`, whose UIKit host re-imposed the status column's safe area. The detail only presents covers and sheets, which don't need a stack.
  - `ContentDetailView` ignores the trailing safe area in an open-display pane (`isInOpenDisplayPane`).
  - Verified on the Duo simulator: the News spread, a news story, a podcast and an article all run to the edge, and titles and chips run under the status icons.
- **Knowledge interstitial:** "Your shelf." / "Saved pieces, chats, and decks open here when you pick one." No action button, because "Ask anything" already sits above the list.
- **Mocks:** `outputs/iphone-duo-final/index.html` (10 screens).

## Decisions

- **R0. The left list ends 40 pt before the hinge; the fold is not a dead zone.**
  - **Measured on the iPhone Duo simulator (iOS 27.2, held wide):** the hinge line is at x 475. The system's division region is x 455–495 (40 pt) with 20 pt margins built in.
  - **Leading lists:** every leading-page list (Podcasts, Articles, Library, Chats) and its divider ends at `hinge − 40` (x 435), which is `fold.minX − 20`.
  - **Trailing page:** starts directly at the list's edge (x 435), with no blank band. Its content flows normally over the fold; scrolling text, images and backgrounds may cross it, as Apple's guidance allows.
  - **One exception:** tappable controls Newsly places at the trailing page's leading edge (the close control and the detail action bar) start right of the hinge line, so a tap never lands on the crease.
  - **News two-column spread:** the gutter between its columns is centred on the hinge. Text runs in both columns; nothing is reserved.
  - **Code (superseded by the 300 pt sidebar; only `newsHingeClearance` remains):** one constant, `OpenDisplayMetrics.listHingeClearance = 40`, measured from the hinge line (the centre of the division region). Without a reported fold, the fallback uses the centre of the content area as the hinge.
  - *Sources:* simulator measurement via `FoldDiagnosticsOverlay`; [Artem Novichkov's examples](https://github.com/artemnovichkov/iPhone-Duo-by-Examples) and [Anton Gubarenko](https://antongubarenko.substack.com/p/ios-271-reservedregion) for the region geometry; [Appetite UI](https://www.appetiteui.com/blog/design-for-iphone-duo-in-figma) and [FoldHunt](https://foldhunt.com/guides/iphone-duo-fold/) for scrolling content passing under the fold while controls stay off it.
- **R1. News column setting.**
  - A per-device reader preference, `AppSettings.openDisplayNewsColumns`: `.single` or `.double`. The default is `.double`. It is stored in the same app-group defaults as the text sizes.
  - It applies only to the News tier on the open display. Phones are always one column.
  - It lives in Settings → Display as "News columns on the open display: One / Two".
  - The row appears only on devices that can open: the current layout is `.openDisplay`, or (iOS 27.1+) `onHingeChange` reports a hinge. Nobody else sees a control that does nothing.
- **R2. Opening a story is always side by side, never an overlay.**
  - In both modes, opening a story reflows News onto the leading page as one column ending 40 pt before the hinge (R0). The story takes the trailing page, starting right at the column edge. Closing the story restores the chosen mode.
  - **Two columns** (default): the spread splits at the fold while nothing is open. Opening a story collapses it to the single leading column, and closing it expands back to two.
  - **One column:** a single reading column at reading width (about 640 pt), centred across both pages while nothing is open. Opening a story moves it onto the leading page.
  - **Reading position survives the reflow:** the leading column scrolls to keep the segment containing the opened story in view, and on close the spread returns to the same segment. Read passage pauses during the reflow (the existing resize pause), so the column change never marks passages read.
  - **Picking another story** from the leading column swaps the trailing page without another reflow.
  - **The slide-over goes away entirely:** `OpenDisplaySlideOverPanel` and its close-button overlay are deleted. The trailing page carries its own close control at its top leading corner, placed right of the hinge (R0).
- **R2a. Attached chat placement follows the same rule.**
  - With a story open, chat takes the leading page: chat | story.
  - With nothing open, News reflows to the leading column and chat takes the trailing page: News | chat.
  - In Podcasts and Articles, chat replaces the detail on the trailing page, with an easy way back to the item (its chat header shows the source). Library keeps today's behaviour.
  - Chat never overlays content.

- **R3. Podcasts and Articles: list + detail, never full cards.**
  - On the open display these tiers do **not** show the phone's full cards, and they don't use the two-column spread. The cards in the current build are the spread experiment and get replaced.
  - The leading page is a Knowledge-style list (R5) from the rail edge to 40 pt before the hinge (R0). The trailing page, from the list edge to the screen edge, is the detail, side by side rather than a slide-over.
  - The list runs in the existing lens page, so read tracking, paging, pull-to-refresh, and continuation behave as today.
  - Selecting a row replaces the detail. Nothing is ever auto-selected (B22), and the selected row stays highlighted.
  - Switching tier keeps the detail only if the item belongs to the new tier; otherwise the pane shows the interstitial.
- **R4. Interstitial (empty right page).**
  - A quiet, branded state with the Newsbuddy mascot, one line of personality, a hint, and one useful action:
    - **Articles:** "Pull up a chair." / "Pick an article and it opens right here." / action: **Listen to Articles**, which plays this lens's narration, the same as the rail player.
    - **Podcasts:** "Tune in." / "Choose an episode and it opens here, ready to play." / action: **Play the Podcasts briefing**.
    - **Read-through state** (lens has no unread items): "All caught up." / "New pieces land here as your briefing updates." / action: **Refresh**.
  - The copy follows `docs/coding-guidelines.md` voice and stays short. Final wording can be tuned in review.
- **R5. One list row for Knowledge, Chats, Podcasts and Articles.**
  - Every list uses `KnowledgeTimelineRow`: 40 pt artwork, one-line serif title (truncation intended, per K5 and the Knowledge single-line-titles decision), one-line subtitle, kicker line, and a trailing accessory. It keeps the hairline dividers and `KnowledgeDayDelimiter` day groups.
  - **Podcasts and Articles rows:**
    - artwork: source thumbnail
    - title: Briefing headline
    - subtitle: the first line of the composed summary
    - kicker: `PODCAST · 51 MIN · NOV 24` / `ARTICLE · 12 MIN · NOV 24`, with the publisher folded in when the width allows
    - read rows dim and desaturate; unread rows carry the existing dot
  - **Chats rows:**
    - artwork: article thumbnail, or the chat icon
    - title: chat title
    - subtitle: the last message preview
    - kicker: `CHAT · 2H AGO`
    - a busy spinner while thinking, replacing today's "THINKING" pill that wraps in narrow widths
    - grouped by day like Knowledge
  - Chat history uses this row everywhere, on phones too. One row component, and the iPhone history then matches the iPhone Knowledge list, which is the look the user referenced. `ChatSessionCard` is deleted if nothing else uses it.
- **R6. Library and Chats layout.** The list is on the leading page, ending 40 pt before the hinge (R0), and the Knowledge navigation stack is the trailing page, starting right at the list edge. Today's build puts the Library divider at the region's leading edge (hinge − 20), so this moves it 20 pt left. The Chats empty pane gets the same interstitial treatment: "Pick up a thread." / "Choose a conversation to keep going."

## Capability parity (nothing is lost)

**Podcasts and Articles rows** (today's card capabilities → their place on the open display)

| Today on the card | On the row / pane |
|---|---|
| Tap card or header → open source | Tap row → detail on the trailing page |
| Swipe: Mark read / Chat / Deck (`BriefingCardSwipe`) | Same gesture and backdrop on the row (the gesture is a generic `UIGestureRecognizerRepresentable`) |
| Context menu: Add to Knowledge / Chat / Create Deck | Same `briefingSourceMenu`, plus **Mark as read** |
| Save button with pending/saved state, success haptic | Trailing bookmark accessory with the same `BriefingSavedSources` state and haptic |
| Mark-read button, light haptic | Swipe, context menu and accessibility action. The detail's own action bar also has save and read controls. |
| Composed summary with source links, discussion link, Dig Deeper on selection | Detail pane: summary, discussion and Dig Deeper live there. The row shows the summary's first line. |
| Length chip, publisher, publication date (no ingestion date) | Kicker, using the same `BriefingSourceCard` fields and date rules |
| Read cards recede | Read rows dim and desaturate |
| Accessibility: full attribution, Briefed time, actions | Same label, from `BriefingSourceCard.accessibilityLabel`, plus named actions |
| Narration "Listen" for the lens | Rail player, plus the interstitial action |

**News**

| Today | Open display |
|---|---|
| Category chips, long-press "Mark all as read" | Unchanged |
| Passage links → story, discussion badge, source menu, Dig Deeper | Unchanged in one or two columns. Opening a story reflows News to the leading page and shows the story beside it (R2). |
| Read passage marking | Unchanged. Two columns use per-segment frames; one column uses the same path as phones. Paused while resizing. |

**Chats**

| Today (Chat History) | After |
|---|---|
| Open a session | Tap row → chat in the trailing pane (open display), or push (phone) |
| Search history (title, subtitle, article, source, topic) | Same filter. The field moves into the list header like Knowledge's search. |
| Delete (context menu) | Context menu plus swipe-to-delete |
| Pull to refresh | Unchanged |
| Processing state | Busy spinner on the artwork, as Knowledge chat rows already do |
| Empty and no-results states | Kept, restyled to the Knowledge empty state |
| Zoom transition source | Kept on phones |

**Knowledge:** unchanged.

## Phases

Each phase is shippable on its own and is checked on the Duo simulator through the E2E launch arguments.

### Phase A — News columns setting

- Add the `AppSettings.openDisplayNewsColumns` key with defaults and persistence, and the Settings → Display row with its visibility rule (R1).
- `BriefingColumnSplit.resolve` takes the preference: `.single` returns nil (one column).
- In both modes, the open shell reflows News to one leading column and shows the story on the trailing page (R2), anchored to the opened segment. One-column reading width is capped and centred while nothing is open.
- Remove `OpenDisplaySlideOverPanel`; give the trailing page a close control. Apply R2a to attached chat.
- *(Superseded: the fixed 300 pt sidebar replaced this.)* Add `OpenDisplayMetrics.listHingeClearance` (R0) to the split model, measured from the hinge line. It applies to the existing Library column too.
- **Tests:**
  - settings persistence and default
  - split resolution for single/double, including R0 (list edge at hinge − 40, trailing page starting at the list edge, News gutter centred on the hinge)
  - the layout resolver returns side by side for an open story in both modes, and the chosen mode once it closes
  - chat placement per R2a
  - the reflow anchor targets the opened segment
- Add an E2E launch argument `newslyE2ENewsColumns single|double` for screenshots.

### Phase B — Podcasts and Articles list + detail

- Add a `BriefingSourceListRow` built on `KnowledgeTimelineRow`, with the swipe gesture, context menu (plus Mark as read), save accessory, selection highlight, read dimming and accessibility (parity table).
- `BriefingLensPageView` gets a row presentation for card lenses again, used only by the open display. Rows follow lens order with plain dividers; no date delimiters (B5).
- The open shell routes the card tiers to list + detail (R3), and News to the spread.
- Add `OpenDisplayInterstitial` with per-tier copy and actions (R4). It replaces `OpenDisplayEmptyPane` for Briefing.
- **Tests:**
  - row kicker and accessibility label from `BriefingSourceCard` (publisher on/off, missing date, missing length)
  - selection survives a tier switch only when the item belongs to the new tier
  - interstitial variant for unread / caught up
  - existing card tests stay green (phones unchanged)

### Phase C — Chats in the shared row

- `ChatSessionHistoryView` renders `KnowledgeTimelineRow` rows grouped by day, with the search field in the header, swipe-to-delete plus context menu delete, a busy spinner, and Knowledge-style empty and no-results states.
- Used by phones (pushed history) and by the open-display Chats column.
- Delete `ChatSessionCard` if it becomes unused.
- Add the Chats interstitial (R6).
- **Tests:**
  - day grouping (pure function)
  - search filter parity: same fields as today
  - delete still calls `deleteSessions`
  - a Maestro flow that opens history, searches, deletes
- Keep the identifiers flows already use, or update the flows.

### Phase D — Docs, laws, validation

- **Laws:**
  - B1 open-display paragraph: News spread with the one/two column preference; opening any story is side by side, never an overlay, and keeps the reading position; Podcasts/Articles list + detail with the interstitial; no auto-select.
  - CH27: on the open display an attached chat takes a page beside its source and never overlays it.
  - B5: rows on the open display, with the full action set.
  - Add a reader-preference line: the column setting is per device and never affects phones.
  - K5 already covers single-line titles. Extend the note to chat rows if needed.
- **Docs:** architecture 11.3a update and a `docs/log.md` entry.
- **Duo simulator screenshots** for the screenshot page, both holds where DeviceHub allows: News one/two columns with and without a story; Articles/Podcasts interstitial and selected; Chats list + chat; Knowledge; phone chat history before/after.
- **Validation:** full unit suite (the known pre-existing `AuthenticationViewModelTests` failures excepted), Opus 5.5 implementation review, Maestro flows where available.

## Risks and open points

- **Swipe inside a narrow row:** horizontal swipe on a list row competes with nothing in the lens page (it has no pager on the open display), but needs a device check for accidental triggers.
- **Lens page now renders three ways** (cards, rows, spread). Keep the branch inside `segmentView`/layout only, so read tracking stays single-sourced.
- **Phone chat history change** is visible on every iPhone. That's intentional (R5), but call it out in release notes.
- **Interstitial copy and the mascot asset** need a quick product review.
- **Wording defaults to settle in review:** the Settings label "News columns on the open display", and the R2 one-column behaviour (side by side while a story is open).
