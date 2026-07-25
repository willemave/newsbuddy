# iOS Architecture & Design Pass — 2026-07-25

A fresh structural read of the iOS client, focused on simplification, runtime performance, and navigation/motion usability. Written after verifying the current tree rather than trusting the July modernization plan, most of which has already landed.

## Where things stand

`docs/initiatives/ios-modernization-2026-07/plan.md` is largely **executed**. Re-verified today:

| Plan item | State |
|---|---|
| Phase 1 subtraction (QuickMic, card stacks, SpriteKit, Discovery, detail VM twins) | Done — every named file is gone |
| `@Observable` migration | 42 files migrated; **2 holdouts** (`BriefingViewModel`, `BriefingDigViewModel`), 11 `@Published` sites left |
| `PaginatedFeed` / `ReadStateCache` / `TaskBag` | Done — `BaseContentListViewModel` and `CursorPaginatedViewModel` deleted |
| Giant decomposition | Done — ContentDetailView 3,204 → 869; `RootTabs.swift` extracted from ContentView |
| Timing hacks | Done — zero `asyncAfter` in the tree |
| Motion/haptics tokens | Done — zero `UIImpactFeedbackGenerator`, 15 `.sensoryFeedback`, 4 raw `.shadow(`, 3 raw `.spring(`, 20 `reduceMotion` sites |
| Zoom transitions | Built (`ContentZoomTransition.swift`) and wired — but see Finding 2 |

App code is now **309 files / 56,623 lines** (excluding generated).

**One correction that matters:** the plan says "Main app targets iOS 18.5." The main app target is now **iOS 26.1** (`project.pbxproj:421`). Only the extension targets are on 18.5. Work is still being planned against an iOS 18 API budget, and the tree shows it — 5 availability gates total, and `tabBarMinimizeBehavior`, `scrollEdgeEffect`, `backgroundExtensionEffect`, `scrollPosition`, `@Animatable`, and the `Tab(...)` builder are all unused.

---

## Finding 1 — Two shells ship, one is reachable

The app carries two complete root experiences:

- **Briefing**: Briefing / Knowledge / Learning, custom floating `CompactTabBar`, More as a sheet.
- **Classic**: Long / Fast / Knowledge / Learning / More on the system tab bar.

Classic is **unreachable in production**. `AuthenticatedRootView.swift:80`:

```swift
ReadingExperiencePolicy.presentationExperience(
    serverExperience: user.readingExperience,
    allowsClassicFallback: E2ETestLaunch.isEnabled   // ← false in prod
)
```

`presentationExperience` returns `.briefing` whenever `allowsClassicFallback` is false. Reinforced on the server: `users.reading_experience` defaults to `"briefing"` (`app/models/db/users.py:24`) and onboarding hard-sets `ReadingExperience.BRIEFING` (`app/services/onboarding/__init__.py:705`).

So the classic shell exists only to keep E2E flows running.

**What it costs.** Verified reachable only from `LongFormTab`/`ShortFormTab`/`MoreTab`, which only instantiate under `if isBriefingExperience { … } else { … }`:

| File | Lines |
|---|---|
| `Views/LongFormView.swift` | 383 |
| `Views/ShortFormView.swift` | 368 |
| `Views/Components/LongFormCard.swift` | 362 |
| `ViewModels/LongContentListViewModel.swift` | 263 |
| `ViewModels/ShortNewsListViewModel.swift` | 230 |
| `Views/LongFormBootstrapStateView.swift` | 214 |
| `Views/ShortFormRows.swift` | 147 |
| `Views/ShortNewsQuickActionsSection.swift` | 142 |
| `Views/LongFormAudioController.swift` | 132 |
| `Views/LongFormActionsView.swift` | 76 |
| `NavigationRestorationModel.swift` | 69 |
| `RootTabSelectionModel.swift` | 54 |
| `Views/ShortFormSetupEmptyState.swift` | 50 |
| `Views/ShortNewsScrollReadTracker.swift` | 30 |
| `RootTab+Availability.swift` | 21 |
| **Total** | **≈2,541** |

Plus the `if/else` in `ContentView.body`, three tab structs in `RootTabs.swift` (~190 lines), the `scrollToTopRequest` plumbing, `longBadge`/`shortBadge`, two of six `NavigationPath`s, and their tests. The only external reference to any of it is a stale comment in `NewsGroupViewModel.swift:143`.

Every `isBriefingExperience` branch — 24 sites across 6 files — is a conditional whose false arm never executes in production.

**Decision (2026-07-25): classic is finished — plan its removal.** Briefing is the only shell.

Removal scope, in dependency order:

1. **Migrate the E2E flows onto the briefing shell.** This is the actual work; the deletion is mechanical. `E2ETestLaunch.readingExperience` (`AppSettings.swift:183`) and the `allowsClassicFallback` parameter both exist solely to serve these flows. Two flows already fail on clean baseline (council "AI Chat", onboarding "Start with sources") — establish which are green *before* starting so new breakage is visible.
2. **Collapse the policy.** Delete `ReadingExperiencePolicy` and `allowsClassicFallback`; `AuthenticatedRootView.applyPrimaryReadingExperience` becomes unconditional or disappears. Whether `ReadingExperience` survives as an API type is a server-side question — the iOS side stops branching on it either way.
3. **Delete the 15 files** in the table above, plus `LongFormTab`/`ShortFormTab`/`MoreTab` from `RootTabs.swift`. Keep `MoreView` — the briefing shell presents it as a sheet (`ContentView.swift:139`).
4. **Shrink `RootTab`** to `.briefing`/`.knowledge`/`.learning`. `RootTab+Availability` and `RootTabSelectionModel` both disappear; `NavigationRestorationModel` disappears (it already no-ops under briefing).
5. **Simplify `ContentView`**: the `if/else` in `body`, four of six `NavigationPath`s, `longBadge`/`shortBadge`, `scrollToTopRequest` plumbing, and all 24 `isBriefingExperience` reads go. `isCompactTabBarVisible` loses its `.longContent, .shortNews, .more` arm.
6. **Retire the tests** that guard classic-only behavior — including `TabCoordinatorViewModelTests.testHandleTabChangeKeepsIncomingLongFormStableWhenAlreadyLoaded`, since the long-form refresh asymmetry it enforces retires with the tab. `ContentListViewModelTests` and `PaginatedFeedTests` stay: `ContentListViewModel` is still live in Favorites/RecentlyRead.

Caveat to check during step 3: `ContentView` passes `currentFastReadItems: { tabCoordinator.shortNewsVM.currentItems() }` into `LongFormTab`. Confirm nothing in the briefing path reads fast-read items before removing `shortNewsVM` from `TabCoordinatorViewModel` and `RootDependencyFactory` (`AppChrome.swift:408`).

---

## Finding 2 — The primary reading path is modal, not navigational

This is the most consequential usability finding.

`BriefingTab` wraps `BriefingView` in a `NavigationStack(path: $briefingPath)`. But `BriefingView` declares **no `navigationDestination`** and never calls `.withContentRoutes`. Opening an article from the briefing goes through `BriefingSourceSheet` (`BriefingSheets.swift:152`):

```swift
.sheet(item: $activeSource) { … }        // BriefingView.swift:147

struct BriefingSourceSheet: View {
    var body: some View {
        NavigationStack {                 // ← a second stack, inside a sheet,
            ContentDetailView(…)          //   inside BriefingTab's stack
                .toolbar { … Button("Done") … }
        }
    }
}
```

`briefingPath` is **never appended to** — verified across the tree. It is a permanently-empty `NavigationPath` that still gets a `NavigationStack`, an `.onChange(of: briefingPath.count)` logger (`ContentView.swift:193`), an always-true `isEmpty` check feeding `isCompactTabBarVisible` (`:273`), and a redundant reset in `openChatSession` (`:302`).

Consequences for how the app feels:

1. **No zoom transition on the main content path.** `ContentZoomTransition.swift` works and is correctly wired — but only into `LongFormView:271` (dead classic tab), `LearningView:243`, and `ChatSessionHistoryView:77`. The briefing → article path is a sheet, so it cannot use it. The best transition in the app is unavailable on the screen users actually use.
2. **No interactive pop.** A sheet gives a downward drag-to-dismiss, not the edge-swipe-back users expect from a reading hierarchy.
3. **Nested `NavigationStack` in a sheet** is a known source of toolbar placement and animation inconsistency, and it's why the sheet needs a manual "Done" button instead of a system back chevron.
4. **No article-to-article stack.** `allContentIds` is passed in and `ContentDetailView` has its own `navigateToNext`/`navigateToPrevious` (`:837`, `:853`) — a bespoke carousel reimplementing what a nav stack gives free.
5. **Deep links can't restore depth.** `ChatNavigationCoordinator` has to route through the Learning tab because briefing has no push target.

**Recommendation.** Give the briefing a real destination: add `.withContentRoutes(tab: .briefing, path: $briefingPath, …)` to `BriefingTab` and push `ContentDetailRoute` instead of setting `activeSource`. Sheets stay for genuinely modal things — dig, narration chapters. This is the change that most directly answers "improve usability with navigation and animations," and the infrastructure for it already exists and is already tested on the chat path.

### Status: done (2026-07-25)

Implemented and verified in the simulator: source links push, edge-swipe-back pops, the compact tab bar hides on push and returns on pop, and lens selection plus scroll position survive the round trip. `BriefingSourceSheet` and `BriefingSourceSheetItem` are deleted. 365 tests pass.

Two things worth recording:

**A latent read-state bug died with the sheet.** `BriefingSourceSheet` constructed `ContentDetailView` without passing `readStateCache`, and that parameter defaults to `ReadStateCache()` — so every briefing article was reading and writing a *throwaway* cache instead of the app's. `withContentRoutes` passes the real one, so cross-surface read state now works from the briefing without any extra change.

**The zoom transition was NOT added, deliberately.** The original recommendation above assumed briefing sources were discrete rows. They are not: the dominant path is inline text links inside a `UITextView` (`BriefingPassageView`), which cannot host a `matchedTransitionSource` — a zoom needs a rectangular source view to morph from. Figures and pullquotes *are* discrete views and could host one, but:

- the same source is frequently reachable from both an inline link and a figure, and both would claim `id: source.id` in one namespace — the tap-from-link case would then morph from a figure that may be off-screen;
- it requires threading a `Namespace.ID` four levels down through `BriefingLensPageView`, which is `Equatable` and deliberately perf-tuned;
- the briefing surface is owned by `docs/initiatives/briefing-tab-2026-07/`.

The standard push animation is the correct, expected behavior for a text link. If a figure-sourced zoom is still wanted, scope it there as its own decision — with a rule for which element owns the transition ID when a source appears twice on a page.

---

## Finding 3 — Hand-rolled tab chrome on a platform that now ships it

`CompactTabBar` (`AppChrome.swift:105`) hides the system tab bar and redraws it:

```swift
.toolbar(.hidden, for: .tabBar)        // RootTabs.swift:130, 169, 214
.safeAreaInset(edge: .bottom) { BriefingCompactTabBarInset(…) }   // ContentView.swift:127
```

…then measures its own height via `onGeometryChange` and plumbs it through a custom `\.persistentBottomChromeInset` environment key so `ChatSessionView:228` can pad around it.

It's well built — it already uses `GlassEffectContainer` + `glassEffect(.regular, in: .capsule)` with `glassEffectID` for the selection morph. But on iOS 26.1 this is re-implementing the native tab bar, which is already Liquid Glass, already floats, and offers `.tabBarMinimizeBehavior(.onScrollDown)` — close to the collapse behavior being hand-built. The cost of the reimplementation is the height-measurement round trip, the environment key, the `isCompactTabBarVisible` matrix in `ContentView:270-281`, and the loss of native tab-bar scroll-edge behavior.

Related: `ContentView` and `RootTabs` still use the legacy `.tabItem { Label(…) } .tag(…)` API. `TabView` gained the `Tab(…)` builder (and `TabView(role:)` for search) in iOS 18; on a 26.1 target there's no reason to stay on the old spelling.

Worth prototyping: native `TabView` + `Tab(…)` + `.tabBarMinimizeBehavior(.onScrollDown)`, and keep `CompactTabBar` only if the native bar genuinely can't express the design. If it can, `persistentBottomChromeInset`, `compactTabBarHeight`, `BriefingCompactTabBarInset`, and `isCompactTabBarVisible` all go away.

---

## Finding 4 — The last `ObservableObject` holdouts are the hottest screen

`BriefingViewModel` (855 lines) and `BriefingDigViewModel` are the only two left. `BriefingViewModel` has **9 `@Published` properties**, including per-frame-ish state:

```swift
@Published var lensStates: [String: BriefingLensState] = [:]
@Published private(set) var isMastheadCompact = false
@Published private(set) var isCategoryStripExpanded = false
```

With `ObservableObject`, any write to any of these invalidates all of `BriefingView` — the pager included. The team clearly hit this: `BriefingChromeCollapseModel` was carved out as a separate `@Observable` specifically to dodge it, and the comment says so:

> "Kept separate from `BriefingViewModel` (and `@Observable` rather than the legacy observation path) so per-frame writes during the collapse window only invalidate the small chrome views that read it — never the pager."

That's a workaround for a problem that disappears when the VM migrates. Observation tracks per-property reads, so `isMastheadCompact` would stop invalidating the pager by construction. The plan deferred these two to the briefing initiative; that deferral has outlived its usefulness — this is now the highest-value remaining perf change, and it retires a bespoke pattern rather than adding one.

Note the current mitigations are good and should stay: `.equatable()` on `BriefingLensPageView`, `@ObservationIgnored` on the VM refs in `TabCoordinatorViewModel`, and `.fixedSize` in the collapse slot to avoid per-frame text re-layout.

---

## Finding 5 — The lens pager uses the UIKit-backed page style

`BriefingView.swift:197` builds the lens pager with:

```swift
TabView(selection: selectedLensBinding) { ForEach(viewModel.pagerLenses…) { … } }
    .tabViewStyle(.page(indexDisplayMode: .never))
```

`.page` is `UIPageViewController` underneath. It eagerly materializes neighbors, gives no lazy-loading control, and its selection binding is the only scroll signal — which is why lens loading has to be driven by explicit `onLoad`/`onFirstPassageVisible`/`onScrolledDown` callbacks threaded through 16 closure parameters.

A `ScrollView(.horizontal)` + `.scrollTargetLayout()` + `.scrollTargetBehavior(.paging)` + `.scrollPosition(id:)` is the current idiom: pages go lazy inside an `LazyHStack`, and `scrollPosition` replaces the binding gymnastics. Worth measuring before committing — the `.equatable()` guard means the current setup may already be acceptable, so this is a "profile first" item, not a certainty.

---

## Finding 6 — iOS 26 surface left on the table

On a 26.1 target, all of these are free and unused:

- `.scrollEdgeEffect` — the briefing masthead is hand-fading via `topScreenEdgeFade()`.
- `.backgroundExtensionEffect` — for the detail hero image bleeding under the chrome.
- `@Animatable` macro — removes hand-written `animatableData` where any remains.
- `.tabBarMinimizeBehavior` — see Finding 3.
- `Tab(…)` / `TabView(role: .search)` — `.searchable` is used **zero** times app-wide, while `KnowledgeSearchView` is a hand-rolled push destination.

None of these are urgent. They matter because the design system predates Liquid Glass and currently gates it in only 5 places (`GlassSurface`, `AddButton`, `CompactTabBar`) — the app is 26.1 wearing an 18.5 costume.

---

## Suggested sequencing

Ordered by value per unit of risk.

| # | Change | Size | Why here |
|---|---|---|---|
| ~~1~~ | ~~Briefing → detail becomes a real `navigationDestination`~~ — **done 2026-07-25** (zoom transition deliberately excluded; see Finding 2) | M | Biggest felt improvement; infrastructure already existed and was tested on the chat path |
| 2 | Migrate `BriefingViewModel` + `BriefingDigViewModel` to `@Observable` (Finding 4) | M | Biggest perf win; retires the `BriefingChromeCollapseModel` workaround pattern |
| 3 | Delete the classic shell (Finding 1, decided) | M | ~2,700 lines and 24 dual-mode branches; real work is migrating E2E flows |
| 4 | Native tab bar prototype; `.tabItem` → `Tab(…)` (Finding 3) | S–M | Prototype first — may retire 4 pieces of custom chrome plumbing |
| 5 | Profile the lens pager; migrate to paging `ScrollView` only if it measures (Finding 5) | S | Evidence-gated |
| 6 | Opportunistic iOS 26 adoption (Finding 6) | S | Fold into whatever screen is already being touched |

**On ordering #1 before #3.** Tempting to delete first for a clean slate, but #1 is user-visible and #3 is not, and #1 is what makes the briefing shell a complete replacement — the E2E migration in #3 is easier against a briefing shell that can actually push a content route. Doing #3 first would mean migrating E2E flows onto a shell that still can't navigate, then migrating them again.

**Verification** stays as the modernization plan defined it: `newslyTests` per change, Maestro visual baselines re-captured after #1 and #3, and Instruments traces around #2 and #5. `TabCoordinatorViewModelTests` guards the long-form refresh asymmetry — note that if #3 proceeds, that test and the asymmetry it protects both retire with the long-form tab.

## Risks

- **#1 and #3 interact.** Once classic dies, `withContentRoutes` loses two of its callers and the route surface simplifies; doing #1 first means writing the briefing destination against the current shared helper, then simplifying it in #3. Accept the small rework — see the ordering note above.
- **#2 touches the surface the briefing initiative owns.** Coordinate with `docs/initiatives/briefing-tab-2026-07/` rather than landing in parallel.
- **E2E flows are the real cost of #3**, not the deletion. Two flows already fail on clean baseline (council "AI Chat", onboarding "Start with sources") — don't let those mask new breakage during migration.
- **Zoom transition vs. the detail swipe carousel** — the same caveat the July plan raised still applies; `ContentDetailView`'s `navigateToNext`/`Previous` gestures may fight zoom dismissal. Prototype on one row type.
