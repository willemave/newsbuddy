# iPhone Duo Support — Plan (2026-11-22)

**Status:** Implemented on `feature/iphone-duo` (uncommitted). Phase 0 was measured on the Xcode 27.2 Duo simulator (2026-11-24). The open-display parts of this plan (Library/Chats destinations, slide-over detail, chat overlay) are superseded by `open-display-revision-plan.md` (2026-11-26). See `docs/log.md` for validation and gaps.
**Scope:** iOS client only (`client/newsly`). No backend, contract, or Share Extension changes.
**Mockups:** PNGs in this folder; the interactive exploration is `outputs/iphone-duo-mockups/index.html` (gitignored, local only).

## Context

The iPhone Duo is a book-style foldable. Facts from Apple's Duo tech talks (111461, 111462, 111463, 111464, 111466):

- **Closed (outer) display:** "wider and shorter than a traditional iPhone." Same size classes as other iPhones; honors supported orientations.
- **Open (inner) display:** regular horizontal and regular vertical size class. It does **not** honor supported interface orientations, so the app appears both wide and upright there.
- **Opening/closing** resizes the running scene in place ("it is still an iPhone app"). Layout must key off size classes, never orientation or `userInterfaceIdiom`.
- **Building with the iOS 27.1 SDK (Xcode 27.1)** gives the full-screen inner layout. Apps built with older SDKs run at "a familiar size and aspect ratio." Custom tab bars and toolbars do not get the system's vertical-bar treatment.
- New APIs (`ArrangementView`, `ReservedRegion`, `onHingeChange`) are iOS 27.1 and need availability gates against our 26.1 deployment target.

Not confirmed by any Apple page we read: point sizes, how the open display's fold is oriented, App Store screenshot requirements, and an April 2027 SDK deadline. Third-party estimates are about 466×678 pt closed and about 890×626 pt open (wide). That implies the natural open hold is wide with a vertical fold, and the upright hold has a horizontal fold. Unverified until Phase 0.

### Current state (verified 2026-11-22)

- iPhone-only (`TARGETED_DEVICE_FAMILY = 1`), iOS 26.1, portrait plus landscape allowed, SwiftUI app lifecycle with a scene manifest.
- No `UIScreen.main` or idiom checks, but two places assume phone geometry:
  - `LearningDeckReaderView` treats `verticalSizeClass == .compact` as landscape (`:48,91`). The open display is regular height in both holds, so the deck reader always uses its portrait layout there.
  - `ChatSessionView.activeScreenWidth` (`:306`) sizes the edge-swipe dismissal from the whole screen's width.
- Root is a two-tab `TabView` (Briefing, Knowledge), each with its own `NavigationStack`, plus a custom floating compact tab bar (`ContentView`, `RootTabs.swift`). The 2026-07-25 architecture pass keeps the custom bar and the Briefing lens pager behind physical-device measurement gates.
- `briefingPath` holds at most one `ContentDetailRoute` (`RootTabs.swift:15`). `ContentDetailRoute` is `Hashable`, not `Codable`.
- Briefing's masthead, pills, category strip and player are custom overlay chrome, centred horizontally.
- **Chat from content mostly leaves the content.**
  - These call `ChatNavigationCoordinator.openReplacingCurrentNavigation`: detail's Start Chat and Dig Deeper (`DetailChatCoordinator.open`) and the Briefing source-card Chat action (`BriefingView.performPendingSourceCommand`). `ContentView.presentChatSession` then resets the Briefing path, switches to Knowledge and pushes the chat.
  - Topic and key-point chats are the exception. They already open attached: a sheet with its own `NavigationStack` (`StructuredSummaryView.swift:107`), no detents and no close handler.
  - Knowledge row chat (`KnowledgeTimelineView`) uses the global path.
- Local toolchain is Xcode 27.0 (27A266a). There is no Duo simulator; that needs Xcode 27.1.

## Decisions

- **D1. Closed display keeps the compact layout.** Today's Briefing card list, chrome and navigation stay as they are. Chrome tuning happens only if Phase 0 measurements show a problem.
- **D2. Open display: tight icon rail, lens list column, detail pane** (mockups: `mockup-open-rail-news.png`, `mockup-open-rail-podcast.png`).
  - **Rail:** about 52 pt wide; icon-only buttons in roughly 32 pt targets.
    - Contents: News, Podcasts and Articles with unread badges, a divider, then Library and Chats, with the Briefing player button pinned at the bottom. The selected item gets a tinted rounded square.
    - Labels: a VoiceOver label and a long-press tooltip on each button; no visible text labels. The three tier icons must be clearly different.
    - During the first run, Welcome / Start Here is the first rail item, sharing the rail with the available lenses as the law requires. An empty tier stays selectable and shows the existing empty state with source management.
  - **List column:** for News, the category chips at the top, then that category's passages with timeline dividers. For Podcasts and Articles, compact rows: thumbnail, publication, title, length and date, unread dot.
  - **Detail pane:** the existing detail views. Selecting a row replaces the pane rather than pushing. **Nothing is ever auto-selected**, because opening a detail marks it read. The selected row stays in the column until the selection changes, even after it becomes read.
- **D3. The shell switches on size classes: horizontal and vertical both `.regular`.**
  - That matches the Duo open display. It excludes Pro Max landscape (regular × compact), iPhone-on-iPad and Mirroring windows, and a compact-width Split View, which all keep the compact layout.
  - Key the shell on the discrete size class only. Within the open display, choose between the chat column and the chat overlay from the measured container width, with hysteresis, so it doesn't flip during the resize animation.
- **D4. Chat started from content stays attached to that content** (`mockup-compact-chat-sheet.png`, `mockup-open-rail-chat.png`).
  - **Compact** (every iPhone and the closed Duo): a sheet over the source.
  - **Open, wide enough for two readable columns:** the list column hides and detail and chat split about 50/50.
  - **Open, narrower:** chat slides in over the trailing part of the detail pane.
  - **Covers:** detail Start Chat, Dig Deeper (including from the full-screen reader, where chat presents over the reader instead of closing it), the chat status banner, the Briefing source-card Chat action, and topic/key-point chats (moved onto the same presenter).
  - **One owner:** a single attached-chat owner above the shell holds the session id, the `ChatSessionViewModel` and the presented state. Sheet, column and overlay only host it. This is what keeps draft, dictation and in-flight sends alive across a fold (see D8). Banner taps reopen this owner rather than replace it.
  - **Stays global (explicit choice):** push-notification chat, Knowledge new chat, session history and Knowledge row chat keep today's global path and law CH10.
- **D5. Selection reuses existing owners; there is no new selection model.**
  - **Briefing selection:** `briefingPath` becomes a typed `[ContentDetailRoute]` (0 or 1 deep), and that array is the selection in both layouts. Detail reports its current id back when edge-swipe changes `currentIndex`, so the route never goes stale. The open-display pane replaces the array directly, without `pushBriefingContentDetail`'s `isEmpty` guard. E2E route injection and `openContentRoute` keep working unchanged.
  - **Tier and lens:** stay in `BriefingViewModel.destination`.
  - **Root destination:** stays in `tabCoordinator.selectedTab`. Rail selection writes it: tiers → `.briefing` plus lens selection, Library → `.knowledge`, Chats → `.knowledge` with `SessionHistoryRoute` in `knowledgePath`. Reselection goes through `tabCoordinator` so scroll-to-top still works.
  - **Why:** activity and read-tracking gates (`updateBriefingActivity`, `isReadTrackingEnabled`, Knowledge `isVisible`) and chat acknowledgement (`knowledgePath` emptying) keep working without changes.
- **D6. The custom compact floating bar and lens pager stay on compact** (the architecture-pass gates still apply).
  - The open-display shell uses neither: the rail and chips pick tiers and categories, with no page swipe in the narrow column.
  - Detail edge-swipe paging is disabled in the pane by passing a single-item id list or a dedicated flag. **Not** via `navigationSurface`: that is analytics context, and `.savedLibrary` changes deck behaviour.
  - The detail's floating back button is hidden at the root of the pane.
- **D7. No iOS 27.1 fold APIs in Phases 1–3.** Fold postures are a later optional phase behind `#available(iOS 27.1, *)`. The shell still keeps floating controls off the fold: the rail sits at the leading edge, and there is no centred floating chrome on the open display. That includes sheets, which become centred form sheets on regular × regular (see Phase 0).
- **D8. Opening or closing the phone must not lose work.**
  - Swapping the shell rebuilds every view below the switch. At risk: `ChatSessionView`'s `@State` view model, which cancels Dig Deeper and voice work and hands polling over to `ActiveChatSessionManager` on disappear; `ContentDetailView`'s index, sheet and covers, and its open-analytics re-fire; `BriefingView`'s dig sheet, deck cover and pending source command.
  - Requirements:
    - Attached chat lives above the switch (D4).
    - The reader, deck and browser covers either live above the switch or sit on an explicit dismiss-on-transition list.
    - The Settings sheet stays attached outside the conditional (it already is, at `ContentView.swift:97`).
    - A re-created detail doesn't re-fire open analytics for the same selection.
    - Exactly one place calls `stopTracking`/`startTracking` for the attached session, so there are no duplicate polling or banner races.
- **D9. Release-SDK rule.** Building with the 27.1 SDK switches the open display to full screen. If the release toolchain moves to Xcode 27.1 before Phase 2 ships, today's compact UI ships stretched to about 890 pt. Phase 0 decides between two options: keep the release SDK on 27.0 until Phase 2 ships, or ship a minimal readable-width guard first.

## Phases

Each phase leaves the app shippable on every current iPhone, subject to D9.

### Phase 0 — Toolchain and baseline evidence

- Install Xcode 27.1 and the iPhone Duo simulator in Device Hub. Build the unmodified app with the 27.1 SDK (locally; this doesn't change the release toolchain, per D9).
- Capture closed and open screenshots, in both open holds, of: Briefing (each tier, first run, an empty tier), detail, the reader, chat, the Learning Deck reader, Knowledge, Settings, and **every sheet** (knowledge actions, share, download, narration chapters, dig, deck create, Settings). Expect centred form sheets on the open display.
- **Fold-survival inventory:** open and close mid-scroll, during Briefing audio, mid-chat with a draft, during dictation and an in-flight reply, and with the reader, deck reader and in-app browser covering the screen. Record what survives.
- Record:
  - real point sizes and fold geometry (via `reservedRegions(kind: .division)` in a debug build)
  - where custom chrome and sheets land relative to the fold
  - left and right safe-area insets on the outer display (the camera is left of the status bar)
  - whether read marking or chrome-collapse measurement misfires during a resize
- Confirm whether Maestro can drive the Duo simulator and its open/close control. Check "Designed for iPhone" on Mac if Mac availability is enabled.
- Decide D9.
- **Exit:** findings logged in `docs/log.md` and this plan amended. No product code changes.

### Phase 1 — Content-attached chat (all iPhones)

- **Attached-chat owner (D4):** detail and Briefing present it as a sheet on compact. Detail adds a chat case to `DetailSheetDestination`, using the existing `pendingSheetDestination`/`onDismiss` handoff, because Start Chat runs from inside the knowledge-actions sheet. Do not add a second `.sheet` modifier.
- **Embedded `ChatSessionView`:**
  - an explicit close control; no edge-back swipe (delete `activeScreenWidth`)
  - `persistentBottomChromeInset` reset to 0 and `.dynamicTypeSize(contentTextSize)` applied
  - `.presentationContentInteraction(.scrolls)`
  - decide whether Start Chat opens at the large detent, since keyboard focus forces it there anyway
- Dig Deeper from the full-screen reader presents chat over the reader without closing it.
- Topic and key-point chats move onto the same presenter (`StructuredSummaryView`).
- Delete `ChatNavigationCoordinator.openReplacingCurrentNavigation` and the replacement-route branch in `ContentView.drainPendingChatRoute`. Global routes keep the queued `open` path. Keep exactly one `stopTracking` call: the view's own or the owner's, not `DetailChatCoordinator.open`'s as well.
- Detail opened from Settings search: the chat sheet presents over the detail inside the Settings sheet. Verify detents and keyboard.
- **Laws:** add a new chat law: content-originated chat presents attached to its source, keeps the source and its reading position reachable, and is the same session in every presentation. CH10 stays as written for global chat.
- **Tests:**
  - `DetailChatCoordinator` / owner presentation state: start, Dig Deeper with a quote, an existing active session, failure, cancellation
  - update `briefing_article_podcast.yaml` (asserts `tab.knowledge` after Start Chat) and `settings_search_detail_chat_handoff.yaml` (asserts `settings.screen` is gone)
  - remove or replace the `openReplacingCurrentNavigation` case in `ChatNavigationCoordinatorTests.swift:114`
  - new Maestro flows for Briefing detail chat and source-card chat that verify dismissal returns to the source at the same position
  - route-injected chat flows use the global path and stay valid
- **Exit:** no content-originated chat changes tabs or clears a path; the existing chat suites pass.

### Phase 2a — Shell switch with existing stacks

- `ContentView` picks the compact `TabView` shell or a regular shell (D3). The typed `briefingPath` (D5), `knowledgePath`, the attached-chat owner and the covers (D8) live above both shells.
- The regular shell is the rail (D2) next to the **existing** Briefing and Knowledge stacks. Rail selection writes `tabCoordinator`.
- Library and Chats host the existing `KnowledgeTab` stack bound to `knowledgePath`, so push-notification and global chat behave as on compact.
- The player button is wired to the narration playback service.
- **Exit:** opening and closing switch shells without losing selection, chat, or covers (the D8 checks).

### Phase 2b — List column and detail pane

- **List column:**
  - News: `BriefingCategoryStrip` plus a column variant of the lens page, without the pager's chrome collapse, pager insets or status-bar fade. Size this extraction in Phase 0.
  - Podcasts and Articles: a new compact row view.
  - Read marking measures against the column's own scroll geometry. Its read boundary is the bottom edge of the category chips (News) or the column top. It passes a nil boundary until a size transition settles, so reflow never marks unread passages.
- **Detail pane:** `ContentDetailView` in a pane-local `NavigationStack`, so the reader, browser and deck presentations still work. Paging and the back button follow D6. News passage links select the news item into the pane. No auto-selection (D2).
- **Learning Deck reader:** choose its layout from measured width or aspect ratio, not `verticalSizeClass`.
- **Sheets:** adapt each one Phase 0 flagged (presentation sizing, or move it into a pane), so none sits centred on the fold.
- **Laws:**
  - B1, second paragraph: an open-display exception. The rail shows icons with accessible names instead of visible names, and the column has its defined read boundary instead of the pills' resting edge.
  - B5: Podcasts and Articles use compact rows in the list column while the detail pane carries the hero.
  - Add a continuity law: opening or closing the device preserves the selected item, active chat, and reading position.
  - Add that a selected source stays listed while selected even after becoming read.
- **Tests:**
  - typed-path ↔ pane selection, including stale-route prevention after edge-swipe on compact
  - rail badge counts, first-run and empty-tier rail states
  - read-boundary suppression during resize
  - Duo open/closed runs: select in the open layout → close → same detail; push on compact → open → same selection

### Phase 2c — Open-display chat column

- The attached-chat owner presents as the detail | chat column or the overlay (D3 width rule with hysteresis).
- Test chat across fold transitions: draft, dictation and in-flight reply survive.

### Phase 3 — Library and Chats panes

- Library: the Knowledge timeline in the list column and the selected item (source detail, chat, deck, narration) in the pane.
- Chats: session history in the column and the selected chat in the pane.
- Decide whether the Learning Deck reader opens in the pane or full-screen. K11 still holds: chat stays secondary to reading.

### Phase 4 — Closed-display tuning (conditional)

Only if Phase 0 shows chrome taking too much height on the shorter outer display: adjust the masthead collapse threshold and hero heights.

### Phase 5 — Fold postures (optional, iOS 27.1+)

- Book pose: snap the detail | chat divider to the division reserved region.
- Tabletop: Briefing audio artwork on the upper half and controls on the lower half.
- Both behind `#available(iOS 27.1, *)`, using `ArrangementView` and `onHingeChange`.

## Validation per phase

- Swift build with warnings treated as errors as the project already does, plus the existing unit suites.
- Focused new tests as listed above.
- Maestro flows for compact on the standard simulator.
- Duo simulator runs for both displays and transitions once Phase 0 confirms Maestro support; otherwise scripted `simctl`/axe runs with recorded screenshots.
- Physical-device confirmation before release, if a device is available.

## Risks and open questions

- **Unverified geometry.** Point sizes and fold orientation are third-party estimates until Phase 0.
- **Release toolchain (D9).** The full-screen open layout comes with the 27.1 SDK whether or not we're ready.
- **Custom chrome vs system bars.** The rail is custom and won't get Apple's vertical-bar behaviour; it must avoid the fold and camera on its own.
- **Lens page extraction.** `BriefingLensPageView` is built around the pager and overlay chrome.
- **Read state.** Pane selection marks items read (B2 eligibility), and reflow during resize can cross the read boundary. Both are addressed in D2 and Phase 2b but need focused tests.
- **Maestro and Duo.** It's unknown whether Maestro can trigger open/close.

## Plan review (2026-11-22)

An Opus 5.5 review of the first draft checked it against the code. Accepted and folded in:

- the geometry assumptions in the deck reader and chat edge swipe
- the existing attached topic-chat sheet
- the B1 visible-names and read-boundary conflict
- the missing Start Here / empty-tier handling
- a new chat law instead of a CH10 edit
- view-owned state lost on a shell swap (D8)
- the non-invertible `NavigationPath`, replaced by a typed 0/1-deep path (D5)
- `tabCoordinator` staying the root owner
- form sheets landing on the fold
- the release-SDK exposure (D9)
- Phase 1 sheet sequencing, inset, reader and tracking details
- the three tests and flows that assume today's handoff
- read marking on pane open (no auto-select)
- not repurposing `navigationSurface`
- splitting Phase 2 into 2a/2b/2c

The size-class gate (D3) was confirmed correct.

## Implementation notes (2026-11-22)

Implemented on `feature/iphone-duo` (uncommitted): Phases 1, 2a, 2b, 2c, and 3. An Opus 5.5 implementation review found two high and four medium issues. All are fixed: Library-pane chat routing, pane back buttons, a poll race on re-host, orphaned Settings/reader chats, retargeting before the pane renders, and lists torn down by the chat column. It also fixed the view-model cache keying and same-session reopen.

**Deviations from the plan**

- Attached chat is presented by host-level sheets (`.root`, `.settings`, `.reader`) rather than a `DetailSheetDestination` case. Detail defers a chat requested from inside its own sheet (`pendingChatRoute`) until that sheet dismisses.
- Library's detail pane keeps Knowledge paging (the route's `allContentIds`). Only the Briefing pane is single-item.
- Selecting a rail item while the chat column is up shows the list again and floats the chat as an overlay, instead of doing nothing.
- The rail's player sits at the bottom of the rail as planned, but it controls the last-selected Briefing lens even while Library is shown.

**Known gaps (D8 dismiss-on-transition list)**

Swapping shells rebuilds the visible detail view. Its full-screen reader, deck reader and in-app browser covers close, the reading position resets, and open analytics fire again. `BriefingView`'s dig sheet, deck cover and pending source command are also dropped. The selected item, tier/lens, root destination and attached chat (draft, dictation, in-flight reply) survive.

**Still open**

- Phase 0, Phase 5, and Phase 4 (conditional on Phase 0).
- Fold geometry, sheets landing on the fold, and real Duo transitions are unverified.
- Whether a selected source stays listed after it becomes read is not verified, and the law doesn't claim it.
- On compact, a root chat sheet requested while another sheet is up may not appear until that chat is closed.
- The News category strip scrolls sideways in the narrow column.

## Phase 0 findings (2026-11-24, Xcode 27.2 / iOS 27.2 iPhone Duo simulator)

Measured with the DEBUG fold overlay (`-newslyDebugFoldOverlay YES`, `FoldDiagnosticsOverlay`), against production state synced locally.

**Geometry**
- Closed (outer): window 466×678 pt (1398×2034 px at 3x). The status bar and camera form a column on the right edge, and the app lays out to its left (the content width is about 382 pt).
- Open (inner): 669×951 pt panel (2007×2853 px). It is presented held wide: window 951×669 pt.
- Hinge reported `fullyOpen` at 180°.
- The fold (division region) is a 40 pt band at x 455–495, inactive while flat.
- Occlusions: the camera at x 677 y 21 (58×37 pt), and the status bar column at x 867 y 0 (84×124 pt). Content ends at x 867.

**Tooling**
- `simctl io <udid> screenshot --display=primary|primary-1` captures the outer and inner screens. Display UUIDs change on every boot.
- Opening and closing is a DeviceHub control. It can't be scripted (DeviceHub runs background-only), and powering screens off with `simctl io screenConfig` does not fold the device.
- axe input does not reach the closed outer display. Drive state with the E2E launch arguments instead; `-newslyE2EOpenChatAttached YES` opens `newslyE2EOpenChatSessionId` attached over `newslyE2EOpenContentId`.

**Observations**
- The compact layout and the attached chat sheet render correctly on the closed display.
- The open layout renders on the inner display.
- The list|pane divider (x≈393) sits about 60 pt left of the fold. The detail action row crosses the fold.
- Chat History rows are cramped at the current Library column width.
- Hero art stops at the status column on both displays.
- Local DB note: the earlier chat 500 and the card-lens decode errors came from a local DB that was 13 migrations behind. They were fixed by the production state sync and are not app bugs.

## Spread revision (2026-11-24)

After using the simulator, the user replaced the list column with a spread:

- **Briefing:** the lens fills the whole open display. There are two columns split at the measured fold (`reservedRegions(kind: .division)` on iOS 27.1+, with equal halves as the fallback), or one column when there is no vertical fold. Articles and Podcasts use full cards again, so the compact row view was deleted. *(Superseded 2026-11-26: only News keeps the spread; Podcasts and Articles return to list + detail. See `open-display-revision-plan.md`.)*
- **Detail:** opening a source slides its detail in over the trailing page (`OpenDisplaySlideOverPanel`). The panel starts past the fold band, runs under the status column, and has a close button. The leading page stays live and outlines the open card.
- **Chat:** with a detail open, an attached chat takes the leading page (chat | article). With nothing open, it slides over the trailing page.
- **Tier switches:** switching tiers on the rail closes the panel.
- **Library:** keeps the split. Its list column now ends at the fold, and the pane is the right page.

Captured on the real Duo simulator with production data: the Podcasts and News spreads, podcast and news detail slide-overs, and chat | article.

## Next revision

The open-display list and column changes requested on 2026-11-26 are planned in [`open-display-revision-plan.md`](open-display-revision-plan.md).
