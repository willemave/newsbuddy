# iOS Modernization & Polish — 2026-07

Comprehensive plan to simplify the Newsly iOS app, make it more idiomatic Swift, improve performance, and level up navigation animations and fine details. Produced from six parallel deep-dives (app shell/navigation, content detail, feeds/lists, state/DI, services, design system/motion), cross-checked against the historical June review, and re-verified with source searches.

## Goal

- Shrink the codebase materially (target: −8,000+ lines) by deleting dead features and collapsing duplicated orchestration.
- Finish the `@Observable` migration (29 of 31 view models are still `ObservableObject`+`@Published`).
- Decompose the giant files (ContentDetailView 3,204 · OnboardingFlowView 1,117 · ContentDetailViewModel 1,093 · SettingsView 875).
- Adopt iOS 18 navigation transitions (zoom from card → detail), remove timing hacks, add scroll-to-top on tab re-tap.
- Systematize motion: tokenized springs/durations/shadows, `.sensoryFeedback` haptics, skeletons, staggered reveals — consistent with the cuddly one-accent brand.
- Measurably improve scroll and detail-open performance (Instruments before/after).

## Current state (evidence summary)

Main app targets **iOS 18.5** (`@Observable`, `NavigationStack`, zoom transitions, `.sensoryFeedback`, `onScrollGeometryChange` all available without gates). ~250 app Swift files; Views 140 files, ViewModels 31 (8.5k lines), Services 33 (6.9k lines).

**Strong foundations (keep):**
- Per-tab `NavigationStack` with typed `Hashable` routes and a centralized `withContentRoutes` extension ([ContentView.swift:425](client/newsly/newsly/ContentView.swift)). Zero `NavigationView` remaining.
- Rich token system: `ReaderPalette` adaptive colors, semantic typography (Lato/Lora), radius tokens (24/14/8), spacing tokens ([DesignTokens.swift](client/newsly/newsly/Views/Shared/DesignTokens.swift)).
- Actor-based two-tier `ImageCacheService` with downsampling; shared `JSONDecoder`; auth refresh with de-dupe coordinator in `APIClient`.
- 100% `@MainActor` coverage on view models; solid Combine cancellable hygiene where Combine is used.

**Debt concentrations:**
- **Dead code never removed** — the June remediation plan's deletion phase (P3) was not executed; all five dead feature clusters still exist (re-verified 2026-07-03 by whole-tree grep).
- **Three pagination stacks** (`BaseContentListViewModel` inheritance, `CursorPaginatedViewModel` inheritance, `BriefingViewModel` bespoke) ≈ 1,000 duplicated lines; `ContentListViewModel` lacks the request-generation guard its sibling has (known race, June idx 9).
- **Read state has no single source of truth**: each list VM keeps its own `items[].isRead` copy synced via `NotificationCenter.contentMarkedAsRead`.
- **Motion is ad hoc**: 27 distinct spring configs, 12+ magic durations, 117 hardcoded shadows, 11 haptic call sites vs ~50 interactive moments, zero `.sensoryFeedback`, zero `navigationTransition`/`matchedTransitionSource`.
- **Timing hacks**: `DispatchQueue.asyncAfter` coupled to animation durations in [ContentDetailSwipeOverlay.swift:227,242,258](client/newsly/newsly/Views/ContentDetailSwipeOverlay.swift) and sheet presentation at ContentDetailView:1765.
- **Perf hotspots**: JSON re-decoded in `ContentDetail` computed properties on every body access (June idx 7/31); always-on 5s polling timers regardless of `scenePhase` (idx 57/58); O(n) day-delimiter recompute per render in ShortFormView; whole-VM observation invalidating entire screens.

## Constraints & standing decisions

- **Long-form tab deliberately does not background-refresh on re-entry** (test-enforced, `TabCoordinatorViewModel:99`). Preserve.
- **One accent + neutrals** color doctrine (terracotta brandPrimary). No new hues; polish work stays near-monochrome.
- **Briefing surface is owned by the in-flight briefing initiative** (`docs/initiatives/briefing-tab-2026-07/`, iOS phases 5–8). BriefingView decomposition guidance below feeds into that initiative's Phase 8 hardening — do not refactor it in parallel here.
- Council "AI Chat" and onboarding "Start with sources" e2e flows fail on clean baseline (pre-existing) — don't chase them as regressions.
- App rename (Newsly → Newsbuddy) is a separate effort; keep names as-is.
- Never commit/push without being asked; run relevant tests per phase.

---

## Phase 0 — Correctness prerequisites (S)

Land the outstanding June P0/P1 bug fixes **before** refactoring the same files, so behavior changes aren't entangled with structure changes:

1. `ContentListViewModel` index-after-await + generation guard (idx 8, 9) and Recently-Read filter no-op (idx 45).
2. Podcast auto-skip stall — replace `.onChange(of: wasAlreadyReadWhenLoaded)` with id-driven change handling (idx 5); detail side-effect guards (idx 29, 30).
3. Add-source sheets discarding input on failure (idx 51, 52, 137).
4. Rendering correctness batch: markdown dark-mode staleness, chat share literal asterisks, heading emphasis flattening, `AppStorage`/`objectWillChange` gaps (idx 35, 39, 40, 81, 100, 44, 139).

Then capture the baseline: full `newslyTests` run, Maestro visual baselines, and one Instruments trace each for Fast Read scroll and detail open (see Verification).

## Phase 1 — Subtraction (M)

Execute the June dead-code pass, re-verified this week. Delete in per-cluster commits, re-grepping each symbol immediately before deletion:

| Cluster | Files | Caveat |
|---|---|---|
| QuickMic | `QuickMicOverlay.swift`, `QuickMicContext.swift`, `QuickMicViewModel.swift` | **First extract `TapToTalkMicButton`** (defined in QuickMicOverlay.swift, live in KnowledgeView, ChatComposerDock, LearningDeckCreateSheet) into its own file |
| Card stacks | `CardStackView.swift`, `SwipeableCard.swift`, `PlaceholderCard.swift`, `LongFormCardStackView.swift`, `ArticleCardView.swift`, `PagedCardView.swift`, `CardStackKeyPointsLoader.swift` | Drop the stale `PagedCardView` comment in `NewsGroup.swift`. Deletion subsumes June bugs idx 13/14/36/37/84/87/89 |
| SpriteKit reveal | `AncientScrollRevealView.swift`, `RevealPhysicsScene.swift` | Also delete `AncientScrollRevealProgressTests.swift` |
| Discovery | `KnowledgeDiscoveryView.swift`, `DiscoveryViewModel.swift`, `DiscoveryService.swift`, `DiscoveryStateViews.swift` | **Keep** `OnboardingSuggestionCard`, `LaneStatusRow`, `DiscoveryPersonalizeViewModel` (live in onboarding). Confirm no feature flag reaches KnowledgeDiscoveryView |
| Detail VM twins | `ArticleDetailViewModel.swift`, `PodcastDetailViewModel.swift` | Never instantiated (re-confirmed) — ContentDetailViewModel is the single detail VM |
| June orphan list | `AuthenticationView`, `ContentListView` (view only — the VM is live in Favorites/RecentlyRead), `FilterBar`, `ContentTypeBadge`, `DownloadMoreMenu`, `SelectableText`, `ChatMarkdownTheme`, `VoiceFFTAnalyzer`, `ChatGPTDeepLinkService` | Re-grep each; one month has passed since verification |
| Dead assets | `Fonts/Inter*.ttf`, `Fonts/Roboto*.ttf` + their `UIAppFonts` Info.plist entries; `Info.plist.backup` | Zero Swift references to Inter/Roboto (verified); DesignTokens uses only Lato/Lora |
| Dead members | Unused DesignTokens colors/fonts, dead `ChatService`/`OnboardingService` methods, 5-copy `buildImageURL` → one shared helper | June idx 50, 69–75, 91–93, 95, 104–106, 112, 113, 126, 131, 133 |

**Do not delete** (agent reports flagged these wrongly; greps show they're live): `TabActivationTiming` (used by both feed views), `CursorPaginatedViewModel` (3 subclasses — handled in Phase 2 instead).

Expected: ~4,500–5,500 lines removed, ~2 MB bundle shrink, several latent bugs gone for free.

## Phase 2 — Idiomatic state layer (L)

The core "more idiomatic Swift" phase. Order matters: stores → list stack → detail → tail.

### 2a. `@Observable` migration
Migrate all `ObservableObject` view models and stores to `@Observable` (`ChatSessionViewModel` and `LearningDeckReaderViewModel` already are). Views change `@StateObject`→`@State`, `@ObservedObject`→plain `let`, `@EnvironmentObject`→`@Environment`. Order by fan-out:
1. Stores/services observed app-wide: `AppSettings`, `ReadingStateStore`, `UnreadCountService`, `ProcessingCountService`, `ToastService`, `ActiveChatSessionManager`, `ChatNavigationCoordinator`, `NarrationPlaybackService`, `VoiceDictationService`.
2. List VMs (as part of 2b).
3. `ContentDetailViewModel` (with Phase 3 decomposition).
4. Everything else (Onboarding, Settings-adjacent, Knowledge, Search, Tweet, narration VMs…).

This fixes the `@AppStorage`-inside-`ObservableObject` publishing gaps by construction, and — because Observation tracks per-property reads — materially narrows view invalidation (rows stop re-rendering when unrelated VM fields change).

### 2b. One pagination model
Replace the three stacks with a single composed component:

```swift
@MainActor @Observable
final class PaginatedFeed<Item: Identifiable & Sendable> {
    private(set) var items: [Item]
    private(set) var phase: LoadPhase   // idle/initialLoading/loaded/loadingMore/error(String)
    // cursor, hasMore, requestGeneration guard, background-refresh merge,
    // optimistic mutation with id-re-resolution after await
    init(loadPage: @escaping (_ cursor: String?) async throws -> Page<Item>) { … }
}
```

- `ShortNewsListViewModel`, `LongContentListViewModel`, `ContentListViewModel`, `NewsGroupViewModel`, `SubmissionStatusViewModel` **compose** a `PaginatedFeed` instead of inheriting.
- Delete `BaseContentListViewModel` and `CursorPaginatedViewModel` once all five are migrated; the request-generation guard and merge logic live in exactly one place.
- Replace `PassthroughSubject` triggers (`refreshTrigger`, `loadMoreTrigger`) and `withCheckedContinuation` bridges with plain async methods.
- Expected: −600 to −800 lines net, and the ContentListViewModel race class disappears structurally.

### 2c. Read-state single source of truth
Introduce one `@Observable ReadStateCache` (absorbing/replacing the notification bridge in `ReadingStateStore`): `isRead(id)`, `markRead(ids)` with optimistic set + rollback, server sync via `ReadStatusRepository`, unread-count derivation for badges. List VMs and detail read from it; delete the `NotificationCenter.contentMarkedAsRead` channel and per-VM local-read bookkeeping. Cross-tab consistency becomes automatic instead of eventual.

### 2d. Task lifecycle & async hygiene
- Prefer `.task(id:)` on views for load-on-appear/cancel-on-disappear (detail load, lens loads).
- Add a tiny `TaskBag` for VM-owned long-lived tasks (polling, deadlines): cancel-and-replace by key, `cancelAll()` in `deinit`. Adopt in the ~12 VMs currently hand-rolling this; give `BriefingViewModel`'s lens tasks cancellation on dealloc.
- Convert `VoiceDictationService` closure callbacks (`onTranscriptFinal`/`onError`) to an `AsyncStream`-based API; extract a `VoiceDictationCoordinator` shared by ChatSession/KnowledgeHub/Onboarding VMs (also fixes the June voice re-entrancy family idx 43/109/125/130 in one place).
- Replace Combine debounce in `SearchViewModel` with `.task(id: searchText)` + `Task.sleep`; after 2b/2c, remaining Combine usage should be ~zero — remove the imports.
- Drop `ObservableObject` conformance from `ChatTimelineReconciler` (pure logic type).

### 2e. Dependency injection — pragmatic, not framework-y
Keep singletons at the composition root, but VMs take dependencies via `init` (the pattern `ShortNewsListViewModel` and `ChatDependencies` already use). Extend `RootDependencyFactory` to build detail/knowledge/settings VMs too; remove direct `.shared` reads from VM bodies. No service-locator container.

### 2f. Standards (write down as `docs/coding-guidelines-ios.md`)
One `LoadPhase` enum (three error styles today); view file ordering per the SwiftUI conventions (environment → lets → state → computed → init → body → subviews); subview-types-over-computed-`some View` for nontrivial sections; `.sheet(item:)` over booleans for model-driven modals; no business logic in `onAppear`/`onChange` closures. The repo currently has **zero** written iOS conventions — this doc is what keeps the next 6 months consistent.

## Phase 3 — Decompose the giants (L)

Apply after 2a so extractions land in the new idiom. Behavior-preserving; each extraction is its own PR-sized change.

| File | Today | Extract | Target |
|---|---|---|---|
| [ContentDetailView.swift](client/newsly/newsly/Views/ContentDetailView.swift) | 3,204 | `DiscussionSheet` (+`DiscussionCommentIndexer` logic type, ~600 lines incl. 8 `@State`s), `DetailChatSheet` (~150), `DetailHeroHeader` (parallax), `DetailSummarySections` (the 6-way summary cascade), `DetailActionBar`, `PodcastAudioController` (episode creation/playback state), `ExpandableSection` (shared component), move `DetailDesign` into DesignTokens | ≤800 |
| [ContentDetailViewModel.swift](client/newsly/newsly/ViewModels/ContentDetailViewModel.swift) | 1,093 | `ShareMarkdownBuilder` (pure, ~300 lines, testable; move `MarkdownItemProvider` with it), `LinkSubmissionCoordinator` (`readLaterLinkStates` is UI state, not domain state) | ≤500 |
| [OnboardingFlowView.swift](client/newsly/newsly/Views/Onboarding/OnboardingFlowView.swift) | 1,117 | One view type per step (choice/audio/loading/suggestions/fastNews/aggregators/reddit) + `OnboardingProgressHeader`; steps currently live as computed-property soup | ≤400 |
| [SettingsView.swift](client/newsly/newsly/Views/Settings/SettingsView.swift) | 875 | `SettingsAccountSection`, `SettingsCouncilSection` (largest), `SettingsDisplaySection`, `SettingsSourcesSection`, `SettingsDebugSection` — one file each | ≤250 |
| [ContentView.swift](client/newsly/newsly/ContentView.swift) | 467 | `LongFormTab`/`ShortFormTab`/`KnowledgeTab`/`MoreTab` structs; `NavigationRestorationModel` (the `restoreIfNeeded` flow); `E2ERouteInjector` gated behind launch-environment check in one place | ≤250 |
| ShortFormView / LongFormView / KnowledgeView | 677/677/643 | Opportunistic section extraction while adopting `PaginatedFeed` (empty/bootstrap states, audio-brief row, day-group list) | ≤400 each |
| BriefingView | 969 | `BriefingHeader`, `BriefingLensPager`, `BriefingControlRow` — **hand this table to the briefing initiative Phase 8**, don't do it here | — |

## Phase 4 — Performance (M)

Code-level fixes first, then verify with Instruments (see Verification):

1. **Memoize `ContentDetail` summary/metadata decoding** — currently `JSONSerialization`+`JSONDecoder` run on every body access, including during drag gestures (June idx 7/31). Decode once in the VM when content loads.
2. **Gate pollers on `scenePhase`**: the 5s `Timer` loops in `UnreadCountService`/`ProcessingCountService` (idx 57/58), `ActiveChatSessionManager`, `BadgeStatsRefreshCoordinator`, and the LongForm 30s loop — pause when backgrounded/inactive.
3. **Pre-compute day groups** in `ShortNewsListViewModel` (`[DayGroup]` published) instead of per-render `calendarDayKey` comparisons over the whole array ([ShortFormView.swift:65](client/newsly/newsly/Views/ShortFormView.swift)).
4. **Image pipeline**: every `CachedAsyncImage` call passes a measured `targetSize` (no `UIScreen.main` guesses — the worst offender dies with ArticleCardView in Phase 1); schedule disk-cache cleanup periodically instead of only at init; keep the prefetch-next-3 pattern; log a real error instead of the silent `catch {}` in `ImageCacheService`.
5. **Row invalidation**: after 2a/2c, feed rows take value inputs (`item`, `isRead`) so marking one row read doesn't re-render the list; keep `ForEach` ids stable (they already are).
6. **Pagination trigger**: replace last-row `.onAppear` (fires on every re-render of the last row) with `onScrollGeometryChange` ~80% depth trigger, or at minimum keep the generation guard in front of it.
7. **Networking**: custom `URLSession` with explicit timeouts (30s request / 60s resource) instead of `.shared`; keep the shared decoder.
8. **KeychainManager thread safety**: `accessGroup` mutation is unsynchronized — make it immutable-after-config or lock it.

## Phase 5 — Navigation & transitions (M)

The app's navigation *architecture* is right; this phase makes it *feel* right.

1. **Zoom transitions (iOS 18)** — the headline win. Add `.matchedTransitionSource(id:in:)` on feed cards and `.navigationTransition(.zoom(sourceID:in:))` on `ContentDetailView` push destinations, so the Long Read card hero grows into the detail parallax hero. Same treatment for: Knowledge chat rows → `ChatSessionView`, and the article reader `fullScreenCover` (zoom works for covers too) from the detail hero. Fall back gracefully for text-only cards (default push).
2. **Kill the timing hacks**: replace the three `DispatchQueue.asyncAfter(0.2)` calls in `ContentDetailSwipeOverlay` with `withAnimation(_:completionCriteria:)` completion blocks (iOS 17), and the 0.3s sheet-scroll delay at ContentDetailView:1765 with a scroll-position-driven approach.
3. **Tab re-tap → scroll to top**: detect re-selection in the tab binding, drive each feed's `ScrollPosition` (iOS 18) back to top with the standard spring; pair with `.sensoryFeedback(.impact(weight: .light))`. (Re-tap must *not* trigger a refresh on the long-form tab — see constraints.)
4. **Sheet audit**: `ContentDetailView` already uses `DetailSheetDestination` enum + `.sheet(item:)` — extend that pattern to the remaining boolean-driven, mutually-exclusive modals; sheets own their `dismiss()` instead of receiving closures where practical.
5. **Scroll-driven chrome**: fade/scale the floating back button on detail as the user scrolls past the hero (single `onScrollGeometryChange`, both detail and chat use the same floating-button component).
6. **E2E route injection** consolidated into the one `E2ERouteInjector` from Phase 3 so production navigation code stops branching on test state.

## Phase 6 — Motion, haptics & fine details (M)

All additive polish, built on the tokens so it stays coherent. Respect `accessibilityReduceMotion` everywhere (currently only 2 screens do).

1. **Motion tokens** in DesignTokens: `AppMotion.press = .spring(response: 0.28, dampingFraction: 0.82)` (the most-used config today), `AppMotion.panel = .spring(duration: 0.3, bounce: 0)`, `AppMotion.subtle = .easeOut(duration: 0.2)`, `AppMotion.emphasized = .spring(response: 0.42, dampingFraction: 0.86)`. Sweep the 27 spring configs and 12 magic durations onto these four.
2. **Shadow tokens**: `ShadowStyle.subtle/.card/.elevated` presets replacing 117 hardcoded `.shadow(...)` calls (three tiers already exist in the wild — this just names them).
3. **Haptics via `.sensoryFeedback`** (replacing the 11 manual `UIImpactFeedbackGenerator` sites and covering the ~25 missing ones): `.selection` on filter chips/segment pickers/toggles/tab re-tap; `.impact(weight:.light)` on card swipe commit and mark-read; `.success` on save-to-knowledge, share complete, subscription added; `.impact(weight:.medium)` on swipe-to-dismiss detail. One small `HapticTrigger` naming convention in the conventions doc.
4. **Content transitions**: `.contentTransition(.numericText())` on tab badges and unread counts; standardize `.contentTransition(.symbolEffect(.replace))` for icon state flips (LongFormCard already does this — make it the norm); insertion/removal transitions on feed rows so mark-read removals animate out (`.transition(.opacity.combined(with: .move(edge: .top)))` under the unread filter).
5. **Staggered reveals**: summary sections on detail and onboarding suggestion cards enter with 50–80ms stagger (small `.delay(Double(index) * 0.06)` on a shared entrance transition); message bubbles slide up from the dock (`.move(edge: .bottom)` + opacity) instead of bare fade.
6. **Skeletons**: one `SkeletonRow`/`SkeletonCard` (shimmer or `.redacted` pulse) replacing spinner-only initial loads on ShortForm/LongForm feeds and detail summary area; unify `ErrorView`/`EmptyStateView`/`DiscoveryEmptyStateView` into one `StateView(role:)` with distinct empty-vs-error iconography.
7. **Card consolidation**: `LongFormCard` remains the one feed card (ArticleCardView deleted in Phase 1); merge `MessageBubble`/`UserMessageBubble` into one parameterized bubble with a shared style.
8. **Type & accessibility**: apply the existing `ContentTextSize`/`dynamicTypeSize` mapping to detail, feeds, and chat (currently only 2 screens); audit concentric radii against the 24/14/8 token set; keep `topScreenEdgeFade` consistent across scrollers.
9. **Onboarding/chat polish**: asymmetric insertions for onboarding cards (`.scale` in, `.opacity` out), stagger council candidate cards, breathing pulse on long-running loading states (already used in 2 spots — reuse that component).

## Phase 7 — Services & extension cleanup (M)

1. **One token-refresh path**: `OpenAIService.fetchAccessToken` delegates to `APIClient`'s `RefreshCoordinator` instead of duplicating refresh logic.
2. **Split KeychainManager (469 lines)** into `KeychainManager` + `TokenRefreshService`/`RefreshCoordinator` + `AuthError` files; fix the access-group mutation (Phase 4 item) here if not already done.
3. **Share extension brand**: shared asset-catalog terracotta color used for the checkmark tint, selected border, and filled submit button (June idx 0/1/10 — currently system blue, the most visible doctrine violation). Keep the extension UIKit; a full shared-framework extraction is not worth it yet, but move the duplicated typography/color constants into a small shared file both targets compile.
4. **APIClient hygiene**: remove/scope the never-removed NotificationCenter observers; keep the auth-failure notification but document its single observer.
5. **NarrationPlaybackService / VoiceDictationService**: after the AsyncStream refactor (2d), collapse the observer nests; both stay `@MainActor` singletons (they're genuinely global audio state).

---

## Sequencing & batching

Dependencies: 0 → 1 → 2 → {3, 4} → 5 → 6, with 7 mergeable any time after 0.

| # | Phase | Size | Suggested PRs |
|---|---|---|---|
| 0 | Correctness prerequisites | S | 3–4 small PRs (per June batching order) |
| 1 | Subtraction | M | 1 PR per cluster (~7 commits/PRs) |
| 2 | State layer | L | stores → PaginatedFeed+list VMs → ReadStateCache → detail VM → tail (5–8 PRs) |
| 3 | Giant decomposition | L | 1 PR per extraction (~10 PRs) |
| 4 | Performance | M | 2–3 PRs + Instruments evidence |
| 5 | Navigation & transitions | M | zoom transitions PR, timing-hack removal PR, tab/scroll+sheet audit PR |
| 6 | Motion & details | M | tokens PR, haptics PR, transitions/skeletons PR, type/a11y PR |
| 7 | Services | M | 3–4 PRs, schedulable in parallel |

Rough total: ~6–8 focused weeks of effort; phases 0–1 are one good week and deliver the largest simplification per hour.

## Verification

- **Per PR**: build + `newslyTests` via XcodeBuildMCP; `TabCoordinatorViewModelTests` guards the long-form refresh asymmetry — it must stay green untouched.
- **Visual**: Maestro visual regression baselines before Phase 1 and re-capture after Phases 3, 5, 6 (`review-and-ship` flow; remember the simulator `serverPort 8000` default when seeding via `generate_test_data.py`).
- **Performance**: Instruments (SwiftUI template + Time Profiler) traces for (a) Fast Read scroll of 200 items, (b) detail open + drag, captured at Phase 0 baseline and re-captured after Phase 4; success = fewer view-body invocations per scroll frame, no JSON decode in the drag hot path, no timer wakes while backgrounded.
- **Behavioral spot-checks after Phase 2**: mark-read in one tab reflects in the other without refresh (new ReadStateCache), filter changes under in-flight loads (generation guard), rollback on airplane-mode mark-read.

## Risks

- **`@Observable` migration breadth** — mechanical but touches most views; mitigate by migrating store-by-store with the app building at every step, and keeping `ObservableObject` conformance temporarily where a store is observed via Combine internals.
- **Pagination unification regressions** — the merge/rollback logic in `BaseContentListViewModel` is subtle; port its tests to `PaginatedFeed` first, then migrate VMs one at a time.
- **Zoom transitions + custom swipe overlay interaction** — the detail's edge-swipe carousel may fight the zoom dismissal gesture; prototype on one card type before rolling out (fallback: keep default push for swipe-carousel entry points, zoom elsewhere).
- **Stale dead-code verification** — a month has passed since the June greps; the plan re-verified the big clusters (2026-07-03) but each deletion PR still re-greps at execution time.
