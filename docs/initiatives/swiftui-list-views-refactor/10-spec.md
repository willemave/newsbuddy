# SwiftUI List Views Refactor Spec (v2)

> Align list/scroll views with modern SwiftUI (iOS 17+), reduce custom scroll plumbing, and standardize view structure without changing product behavior.

## Summary

Refactor five list/scroll-heavy screens to use current SwiftUI APIs (`NavigationStack`, `scrollPosition`, `onScrollPhaseChange`, `scrollTargetLayout`) and to follow local SwiftUI composition patterns. The plan favors MV (no new view models), consistent view ordering, and separation of large view bodies into small subviews or files.

## Goals

- Remove custom scroll-position detection and prefer iOS 17 scroll APIs.
- Simplify list rows by using native `List` behavior or commit to custom `ScrollView` layouts.
- Modernize navigation to `NavigationStack` and centralize destinations.
- Improve file organization for large views without altering UX.
- Preserve scroll position where possible (tab switch, refresh, background/foreground).

## Non-goals

- No new features or UX redesign.
- No changes to data fetching, models, or network behavior.
- No new view models unless a view already uses one.

## Constraints

- Deployment target must be iOS 17+ (required for `scrollPosition`, `onScrollPhaseChange`, `scrollTargetLayout`, `defaultScrollAnchor`).
- Keep existing behavior and accessibility intact.
- Avoid introducing nested scroll views with the same axis.

## Cross-cutting patterns

### View structure and state (swiftui-view-refactor)
- Order declarations: Environment -> lets -> @State -> computed vars -> init -> body -> subviews -> helpers.
- Default to MV: use `@State`, `@Environment`, `task`, `onChange`. Keep logic in services/models.
- If a view model exists, make it non-optional and initialize in `init` via `@State`.

### List vs ScrollView decision (swiftui-ui-patterns)
- Use `List` for settings and feed rows where row semantics, selection, and swipe actions matter.
- Use `ScrollView + LazyVStack` when you need custom card layouts or mixed content.
- Do not heavily style `List` to mimic cards unless swipe actions are required.

### Navigation
- Replace `NavigationView` with `NavigationStack`.
- Prefer `NavigationLink(value:)` + `navigationDestination(for:)` for clarity.

### Scroll position and refresh
- Use `.scrollTargetLayout()` in scroll content.
- Use `.scrollPosition(id:)` for position tracking.
- Use `.onScrollPhaseChange` to trigger side effects only when scrolling stops.
- Keep stable row IDs to preserve scroll position.

### Chat scroll behavior (must preserve)
- Auto-scroll to bottom only if user is already at bottom.
- Preserve scroll position when new messages arrive.
- Maintain scroll offset when prepending older messages.
- Keep keyboard avoidance/input bar behavior intact.

## View-by-view plan

### 1) SettingsView
**Current:** `NavigationView`, inline destinations, manual chevrons.

**Plan:**
- Replace `NavigationView` with `NavigationStack`.
- Use `NavigationLink(value:)` + `navigationDestination(for:)`.
- Remove manual chevrons; rely on `NavigationLink` styling.
- Extract sections into computed properties for readability.
- Keep `Form` and section structure as-is.

**Acceptance:**
- Navigation still reaches all destinations.
- Visual layout and section ordering unchanged.

### 2) ShortFormView
**Current:** Custom `GeometryReader` scroll detection and unused `ScrollViewReader`.

**Plan:**
- Remove custom `ScrollPositionDetector` and preference keys.
- Replace with `scrollPosition(id:anchor:)` and `onScrollPhaseChange`.
- Keep infinite scroll and pull-to-refresh logic intact.
- Ensure `markItemsAboveAsRead` runs only when scroll is idle.

**Acceptance:**
- Mark-as-read behavior matches current behavior.
- Load-more trigger still fires at end-of-list.
- No jumpy scrolling or flicker.

### 3) ChatSessionView (+ ChatScrollView)
**Current:** `UIViewControllerRepresentable` wrapping `UIScrollView`, manual size invalidation.

**Plan:**
- Replace with native `ScrollViewReader` + `ScrollView` + `LazyVStack`.
- Use `.defaultScrollAnchor(.bottom)` and `scrollPosition(id:)`.
- Remove `ChatScrollView.swift` if no longer needed.
- Ensure the chat behaviors listed in “Chat scroll behavior (must preserve)” are maintained.

**Acceptance:**
- Chat auto-scrolls to newest message.
- Loading older messages does not break scroll position.
- Performance stable for long threads.

### 4) LongFormView
**Current:** `List` heavily customized to look like cards, manual dividers/chevrons.

**Decision:** Swipe actions are required, keep `List` and simplify styling.

**Plan (List):**
- Remove manual dividers and chevrons.
- Minimize row styling; use `.listRowSeparator(.hidden)` only if required.

**Acceptance:**
- Taps still navigate to detail.
- Scroll position retained across refresh.

### 5) KnowledgeDiscoveryView
**Current:** Large single file with inline state views and manual spacers.

**Plan:**
- Split large view into subviews or separate files:
  - `DiscoverySuggestionCard.swift`
  - `DiscoveryStateViews.swift` (loading, error, empty)
  - `DiscoveryRunSection.swift`
- Replace `Spacer().frame(height:)` with padding.
- No scroll-position persistence required; do not add `scrollPosition` unless needed for future behavior.

**Acceptance:**
- Layout visually unchanged.
- State views still appear correctly.
- File size reduced and navigation simpler.

## Resolved decisions

- LongFormView keeps `List` because swipe actions are required.
- ChatSessionView must preserve current auto-scroll and keyboard behaviors.
- KnowledgeDiscoveryView does not need scroll-position persistence.

## Implementation plan (phased)

1) **Preflight**
   - Confirm deployment target is iOS 17+.
   - Scan each view for existing view model usage and confirm they remain non-optional.

2) **Low-risk modernization**
   - SettingsView -> `NavigationStack` + destinations.

3) **Scroll API replacements**
   - ShortFormView -> remove custom scroll detection, use `scrollPosition`.
   - ChatSessionView -> replace `ChatScrollView` with native SwiftUI.

4) **List vs ScrollView decision**
   - Resolve LongFormView swipe-action requirement.
   - Implement List or ScrollView path accordingly.

5) **Large file cleanup**
   - Split KnowledgeDiscoveryView into subviews/files.
   - Remove manual spacer sizing in favor of padding.

## Test plan

- Pull-to-refresh works on all affected lists.
- Infinite scrolling triggers correctly (ShortForm/LongForm).
- Scroll position preserved on tab switch and app background/foreground.
- Mark-as-read behavior is unchanged (ShortForm).
- Navigation destinations render correctly (Settings).
- Chat auto-scroll behaves on new message append.
- VoiceOver/Accessibility labels remain intact.
- Memory remains stable during long scrolls.

## Rollback strategy

- Each view is refactored independently; revert by file if behavior regresses.
