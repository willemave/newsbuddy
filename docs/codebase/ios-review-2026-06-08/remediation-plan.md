# Newsly iOS — Remediation Plan

## Executive Summary

The codebase is structurally healthy: one accent + neutrals doctrine is broadly respected, the token system is rich, and the view-model layer is consistent. The debt concentrates in two areas. First, a small set of **real concurrency/state-correctness bugs** in the most-used view models (`ContentListViewModel`, `ContentDetailViewModel`) plus user-facing flow bugs (add-source sheets, podcast auto-skip, Recently-Read filtering). Second, an unusually **large volume of dead code** — five entire unwired features (QuickMic, two card-stack chains, the SpriteKit AncientScrollReveal scene, and the Discovery suggestions cluster) and roughly a dozen orphaned view/view-model files, plus many unused design tokens.

Only two findings carry genuine crash potential (the index-after-await races in `markAsRead`); most are correctness, consistency, or perf concerns. A major efficiency win in planning: several confirmed bugs and token violations live *inside dead files*, so the dead-code pass eliminates them for free — provided bugs are fixed first and dead code is removed last, after confirming nothing dynamically references it.

**Sequencing principle:** fix correctness bugs first → batch the token/consistency sweeps → remove dead code last (it subsumes a chunk of the bug and token work). Within each phase, respect view-model-before-view ordering.

---

## P0 — Correctness bugs (do first)

### Concurrency & stale-state bugs in list/detail view models
*Findings: 5, 8, 9 — `ContentListViewModel.swift`, `ContentDetailViewModel.swift`, `ContentDetailView.swift`*

**What & why.** These are the only findings with crash/data-corruption potential, in the two most heavily used view models.

- **idx 8 — `markAsRead` reuses a pre-`await` index.** `index` is captured at line 141 before `await markContentAsRead` (line 143), then reused at lines 144/159 for subscript and `remove(at:)`. During the suspension, concurrent `loadContent()` tasks (spawned by every filter `didSet`) can shrink/reorder `contents`, causing out-of-bounds crash or wrong-item removal. **Fix:** re-resolve by id after the await — `guard let i = contents.firstIndex(where: { $0.id == contentId }) else { return }`.
- **idx 9 — overlapping `loadContent()` with no generation guard.** Four `@Published` filter `didSet`s each spawn `Task { await loadContent() }`; multi-filter changes race, a slow earlier response overwrites `contents`, and the shared `isLoading` flips early. **Fix:** copy the `requestGeneration` pattern already implemented in sibling `BaseContentListViewModel` (capture a generation at start, `guard generation == requestGeneration` before applying results / clearing `isLoading`). Apply to `loadMoreContent()` too.
- **idx 5 — podcast auto-skip silently stalls.** The skip in `ContentDetailView.swift:455` is driven by `.onChange(of: viewModel.wasAlreadyReadWhenLoaded)`, but `updateContentId` never resets that `@Published` between loads. Navigating read→read emits no change, so the cascade stops on the first read item. **Fix:** reset `wasAlreadyReadWhenLoaded` (or drive the skip off `content?.id` changing) inside `updateContentId` so each load re-emits.

**Batch:** one PR. idx 8 and 9 are the same file and same root cause (suspension races); idx 5 is the same `ContentDetailViewModel`/detail navigation surface and pairs naturally with the P1 detail-side-effect fixes below.

---

## P1 — High-value fixes

### Detail-load side effects fire for passed-through items
*Findings: 29, 30 — `ContentDetailViewModel.swift`*
**What & why.** `loadContent` spawns three detached `Task`s (lines 120–132) that outlive the cancelled `.task(id:)` scope. `markFetchedContentAsReadIfNeeded` issues the server mark-as-read at line 228 **before** the staleness guard at line 231, so fast cascade-skip marks transited unread items read server-side and can double-decrement counts. **Fix:** make the child work structured (so it cancels with the parent) or guard the *network* side-effect (not just the in-memory mutation) on the request still being current. **Sequence right after the idx 5 auto-skip fix** — same flow.

### Add-source sheets dismiss & discard input on failure
*Findings: 51, 52, 137 — `FeedSourcesView.swift`, `PodcastSourcesView.swift`*
**What & why.** Both Add buttons call `resetAddForm()` + `showAddSheet = false` unconditionally after `addConfig`, which never throws (it only sets `errorMessage`). On failure the sheet closes and the user's typed URL/name/limit are wiped. **Fix:** gate reset+dismiss on `viewModel.errorMessage == nil` (mirror the existing `SourceDetailSheet.saveChanges()`). Also (idx 137) reject non-empty non-numeric limit input with an inline error instead of silently treating it as no-limit. Small, high value.

### Recently-Read filter sheet no-ops and loads wrong mode
*Finding: 45 — `RecentlyReadView.swift`, `ContentListViewModel.swift`*
**What & why.** Changing any filter flips the VM out of recently-read mode (the `didSet`→`loadContent` sets `isRecentlyReadMode = false`), loads the regular list under the sheet, and `loadRecentlyRead` ignores all filter args on dismiss — so the filter UI does nothing and triggers a redundant wrong-mode fetch. **Fix:** either make `loadRecentlyRead` honor the filters, or guard the `didSet`-driven `loadContent` while in recently-read mode. **Do in the same `ContentListViewModel` pass as idx 9** (same didSet machinery).

### Share Extension uses system blue instead of brand terracotta
*Findings: 0, 1, 10 — `ShareExtension/ShareViewController.swift`*
**What & why.** The extension links no design tokens, so its three most prominent accent surfaces fall back to system blue: option-row checkmark tint (line 555), selected-row border (line 603), and the `.filled()` submit button (no `baseBackgroundColor` set). This is the most visible doctrine violation in the app. **Fix:** introduce one shared `brandPrimary` `UIColor` (ideally a shared asset-catalog color matching #af3535/#e37777) and apply it to all three. Verified: no SwiftUI color tokens are reachable from this target, so a local/asset color is required.

### Markdown & chat rendering correctness
*Findings: 35, 39, 40, 81, 100 — `SelectableMarkdownView.swift`, `ChatShareSheet.swift`, `ArticlePreviewCard.swift`, `ChatTimelineReconciler.swift`*
**What & why.** Independent reader/chat defects: markdown colors go stale on light/dark toggle (no `colorScheme` dependency, so `updateUIView` isn't re-invoked — idx 35); chat share sends literal `**Title**` asterisks because Markdown is passed as a plain `String` to `UIActivityViewController` (idx 39); inline emphasis inside headings is flattened because `applyHeadingStyle` blanket-overwrites per-run `.font` (idx 81); empty-string `source` renders a blank icon row, unlike the summary branch which guards `!isEmpty` (idx 100); and the reconciler drops process-summary rows whose text lives only in `processLabel` (idx 40). **Fix:** add `@Environment(\.colorScheme)`; share plain text / `NSAttributedString`; merge heading scaling per-run; add the `!isEmpty` guard; broaden the reconciler keep-predicate.

### AppStorage/objectWillChange publishing gaps
*Findings: 44, 139 — `AppSettings.swift`, `SettingsView.swift`, `SubmissionStatusViewModel.swift`*
**What & why.** `@AppStorage` inside an `ObservableObject` doesn't fire `objectWillChange`, so text-size sliders don't reliably refresh observers (the author already hand-publishes for `readerPalette`). The submission badge has the same UserDefaults-without-`@Published` pattern. **Fix:** migrate to `@Published`-backed-by-UserDefaults (or wrapper setters that `objectWillChange.send()`), applied consistently.

### Always-on polling timers + in-body JSON re-decoding (perf)
*Findings: 7, 31, 57, 58 — `UnreadCountService.swift`, `ProcessingCountService.swift`, `ContentDetail.swift`, `ContentDetailView.swift`*
**What & why.** Two singletons run repeating 5s `Timer`s that fire network requests and mutate `@Published` state for the whole app lifetime regardless of scene phase (idx 57, 58). Separately, `ContentDetail`'s summary/metadata computed properties run `JSONSerialization` + `JSONDecoder` uncached on every body access, re-decoding during drag gestures on the detail screen (idx 7, 31 — note idx 7 only decodes the single matching variant, not several). **Fix:** gate timers on `scenePhase == .active`; memoize/decode the summary & metadata once (in the VM or `.task`). Verify with Instruments after.

---

## P2 — Targeted cleanups

### Card-stack swipe bugs — fix by deletion, not in place
*Findings: 36, 37, 84, 87, 89 — `LongFormCardStackView.swift`, `PagedCardView.swift`*
**What & why.** Real bugs (haptic never fires because it compares `targetIndex` to an already-mutated `currentIndex`; index-keyed `ForEach` leaks per-card `@State`; duplicated `onChange` guard; read-after-write load-more). **But both files are confirmed dead** (idx 4, 17). **Do not patch** — these are subsumed by the dead-code pass. Listed only so the reviewer knows they're real-but-moot.

### Voice-capture re-entrancy & recording-state races
*Findings: 43, 109, 125, 130 — `OnboardingViewModel.swift`, `DiscoveryPersonalizeViewModel.swift`, `TweetSuggestionsViewModel.swift`, `KnowledgeHubViewModel.swift`*
**What & why.** The 30s auto-stop timers can collide with a manual stop and surface a spurious "recording failed" that clobbers the legitimate `.transcribing` state (idx 43, 130); `TweetSuggestionsViewModel.startVoiceRecording` lacks the guard its chat sibling has (idx 109); `KnowledgeHubViewModel` has two competing sources of truth for recording flags (idx 125). **Fix:** add an `isStopping`/entry-state guard so concurrent stops early-return; pick a single source of truth for recording flags. Consistent pattern across all four.

### Misc state/gesture/lifecycle nits
*Findings: 46, 48, 60, 86, 94, 98, 99, 119, 134*
Independent small fixes: learning-deck list reorders on every 3s poll — replace-in-place instead of `remove`+`insert(at: 0)` (idx 46); long-content tab never background-refreshes unlike short-news (idx 60); full-image single-tap dismiss fights double-tap-zoom (idx 48); detached audio Tasks in `ShortFormView` not cancelled on disappear (idx 134); `ForEach` id collisions on text/prefix-derived ids (idx 86, 94) and council expert ids (idx 119); `ChatSessionView` edge-back gesture uses deprecated `UIScreen.main` + hand-tuned `asyncAfter` (idx 98, 99). No ordering dependency.

### Non-optional summary decode fields
*Finding: 38 — `StructuredSummary.swift`*
Non-optional array fields + `try?` mean a single missing backend key voids the *entire* summary (user sees nothing). **Fix:** `decodeIfPresent(...) ?? []` via custom `init(from:)`.

### Font-weight overrides & faux-italic
*Findings: 11, 12, 63, 64*
`.fontWeight(.bold)` stacked on `appLargeTitle`/`appTitle3` (already `.semibold`) fights the token (idx 11, 12, 63 — 63 may be an intentional news-vs-other distinction, confirm). `.italic()` on regular sans/serif tokens synthesizes faux-italic across ~9 files instead of using the real `appSansItalic`/`appSerifItalic` faces (idx 64). Pure token sweep, no behavioral risk.

---

## P3 — Token sweeps, dead code, and larger refactors (do last)

### Magic-number spacing/metrics
*Findings: 13, 14, 65, 66, 67, 68*
Literal `spacing: 20` (→ `CardMetrics.cardSpacing`), `.padding(.horizontal, 12)` (→ `Spacing.rowHorizontal`), `.padding(.horizontal, 16)` (→ `Spacing.fastReadHorizontal`), hand-rolled list-row resets (→ `appListRow()`). One mechanical sweep with per-site confirmation. **idx 13/14 (ArticleCardView corner radius/hero height) live in dead code — skip; they vanish with deletion.**

### Raw color literals for scrims/code backgrounds
*Findings: 61, 62, 83*
Floating back-button scrim and code-block backgrounds use bespoke RGB literals (all neutral greys, not hue violations). Add a `UIColor` surfaceContainer / overlayScrim token and reference it.

### Dead-code removal (the big subtraction — run after bug fixes land)

> **Sequencing & safety:** Run this phase *after* the P0/P1 bug fixes, and confirm via whole-tree grep (already done in findings) that nothing references these via storyboard/string/dynamic lookup. Deleting the card-stack and Discovery clusters also removes a chunk of P2/P3 bug and token work for free.

- **QuickMic feature** (idx 2, 53, 54): delete `QuickMicOverlay.swift`, `QuickMicContext.swift`, `QuickMicViewModel.swift`. **⚠️ `TapToTalkMicButton` lives in `QuickMicOverlay.swift` and IS live — move it to its own file first.**
- **Card-stack chains** (idx 3, 4, 17, 90): delete `CardStackView.swift`, `SwipeableCard.swift`, `PlaceholderCard.swift`, `LongFormCardStackView.swift`, `ArticleCardView.swift`, `PagedCardView.swift`, `CardStackKeyPointsLoader.swift`. Subsumes idx 13/14/36/37/84/87/89/90. Drop the stale `NewsGroup.swift` PagedCardView comment.
- **AncientScrollReveal / SpriteKit** (idx 18, 41, 42): delete `AncientScrollRevealView.swift`, `RevealPhysicsScene.swift`.
- **Discovery cluster** (idx 23, 47, 121–123, 127, 128): delete `KnowledgeDiscoveryView.swift`, `DiscoveryViewModel.swift`, `DiscoveryService.swift`, `DiscoveryStateViews.swift` and the Discovery-only components. **⚠️ `OnboardingSuggestionCard`/`LaneStatusRow` are shared with live onboarding — keep them. Confirm Discovery isn't feature-flagged before removing.** Subsumes all the latent Discovery VM bugs.
- **Orphaned views/VMs** (idx 15, 16, 19–22, 24–28, 33, 34, 49, 55, 77, 78, 103, 132, 136, 138): `AuthenticationView`, `ContentListView` (keep the VM), `ArticleDetailViewModel`, `PodcastDetailViewModel`, `NewsGroupViewModel`, `KnowledgeDiscoveryView`, `FilterBar`, `ContentTypeBadge`, `DownloadMoreMenu`, `SelectableText`, `ChatMarkdownTheme` (+ MarkdownUI dep if unused), `VoiceFFTAnalyzer`, `ChatGPTDeepLinkService`.
- **Dead members/tokens/dupes** (idx 50, 69–75, 91–93, 95, 104–106, 112, 113, 126, 131, 133): dead `selectedContentTypes`/`markAllAsRead`; dead `ChatService`/`OnboardingService` methods + models; dead `StructuredSummaryView` members; unused Color/Font/UIFont tokens in `DesignTokens.swift` (isolated clean sweep); unify the 5-copy `buildImageURL` helper.

### Monolithic views & fragile plumbing
*Findings: 32, 59, 76, 79, 80, 82, 85, 88, 101, 102, 107, 108, 110, 115, 116, 117, 120, 124*
Larger/lower-urgency: decompose the 3200-line `ContentDetailView` (idx 32) and 600-line `LongFormView` (idx 88); replace timing-based share-sheet presentation racing (idx 80) and the 30fps `LandingView` title rebuild (idx 115); plus assorted cosmetic/low-confidence items. Do opportunistically when touching the relevant files; none are blocking.

---

## Recommended batching order

1. **PR 1 (P0):** `ContentListViewModel` races (idx 8, 9) + Recently-Read filter (idx 45) + podcast auto-skip (idx 5).
2. **PR 2 (P1):** `ContentDetailViewModel` detail side effects (idx 29, 30) — builds on PR 1's skip fix.
3. **PR 3 (P1):** Add-source sheets (idx 51, 52, 137) — small, isolated.
4. **PR 4 (P1):** Share Extension accent (idx 0, 1, 10).
5. **PR 5 (P1):** Rendering correctness (idx 35, 39, 40, 81, 100) + AppStorage publishing (idx 44, 139).
6. **PR 6 (P1, perf):** Polling timers + JSON decode caching (idx 7, 31, 57, 58) — verify with Instruments.
7. **PR 7 (P2):** Voice re-entrancy (idx 43, 109, 125, 130) + summary decode (idx 38) + misc nits (idx 46, 48, 60, 86, 94, 98, 99, 119, 134).
8. **PR 8 (P3, token sweep):** Font weights/italics (idx 11, 12, 63, 64) + spacing/metrics (idx 65–68) + color literals (idx 61, 62, 83).
9. **PR 9+ (P3, dead-code removal):** the feature-cluster and orphan deletions above — **last**, after a final grep confirms no dynamic refs. Split into per-cluster commits for reviewability.
