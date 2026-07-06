# iOS Coding Guidelines

These conventions apply to the SwiftUI app and share extension under
`client/newsly`. Keep feature-specific architecture notes in
`docs/codebase/client/`; use this file for durable Swift patterns.

## State And Observation

- Use `@Observable` for new view models and UI-facing stores. Keep UI-bound
  observable types `@MainActor`.
- Views that own an observable root store it with `@State`. Child views receive
  observable objects as plain `let` values, using `@Bindable` only when the child
  writes through bindings.
- Shared observable stores should be passed from the composition root or read via
  `@Environment` after migration. Avoid direct `.shared` reads inside view-model
  bodies.
- Mark task handles, services, caches, delegates, loggers, and other non-UI
  implementation details with `@ObservationIgnored`.
- Prefer injected dependencies in `init` over hidden singleton lookups. The root
  dependency factory is the place to bridge singletons into feature view models.

## Load Phases

Use one UI-loading vocabulary for new async state:

```swift
enum LoadPhase: Equatable {
    case idle
    case initialLoading
    case loaded
    case loadingMore
    case empty
    case error(String)
}
```

- Use `error(String)` for user-facing state. Convert `Error` values to display
  strings at the boundary where the async operation is handled.
- Keep pagination truth separate from display state: cursor, `hasMore`, and
  request-generation guards belong in the pagination model, not scattered across
  views.
- When touching legacy surfaces, migrate local variants such as `LoadingState`,
  nested `LoadState`, and boolean `isLoading`/`errorMessage` pairs toward
  `LoadPhase` instead of adding another shape.

## View Structure

Order SwiftUI view files this way:

1. `@Environment` and environment-derived values
2. `let` inputs and dependencies
3. `@State`, `@Binding`, `@FocusState`, and other local state
4. Computed values used by the body
5. `init`
6. `body`
7. Private subviews, builders, and helper methods

- Keep `body` declarative. It should describe layout and route events to named
  methods, not perform business logic inline.
- Prefer small dedicated `View` types over computed `some View` properties for
  nontrivial sections, repeated UI, or views with their own state, tasks, or
  modifiers.
- Computed `some View` is fine for tiny static fragments that read clearly at the
  call site.
- Put model transformation in view models or pure helper types when the result is
  reused, expensive, or needed by tests.

## Side Effects And Tasks

- Prefer `.task(id:)` for async loading tied to view identity. It gives SwiftUI a
  cancellation boundary when the id changes or the view disappears.
- Use `onAppear` for local presentation work such as focus, animation bootstrap,
  or forwarding a simple lifecycle event to the view model. Do not put multi-step
  business logic in `onAppear`.
- Use `onChange` to pass the changed value to a named method. Keep branching,
  networking, persistence, and retry logic out of the closure.
- View-model-owned tasks should be cancel-and-replace by purpose, and long-lived
  view models should cancel outstanding tasks in `deinit`. Use `TaskBag` for
  keyed task ownership instead of adding separate `Task` properties for each
  foreground send, debounce, poller, or deadline.
- Prefer `AsyncStream` event APIs for service callbacks that several view models
  consume. `VoiceDictationCoordinator` is the shared adapter for
  `SpeechTranscribing` events.

## Sheets And Navigation

- Prefer `.sheet(item:)` for model-driven modals, mutually exclusive modal
  destinations, and sheets with payloads. Use an `Identifiable` enum when one
  screen owns several sheet destinations.
- Boolean sheet flags are only for one-off, payload-free presentation state.
- Let sheets call `dismiss()` themselves when practical instead of receiving
  pass-through close closures.
- Keep route identity typed and stable. New push routes should follow the
  existing `Hashable` route pattern used by the tab `NavigationStack`s.

## Motion And Feedback

- Use shared motion and design tokens for durations, springs, shadows, radii, and
  spacing. Do not add one-off animation constants unless the interaction clearly
  needs a new token.
- Use `appShadow(_:)` with `ShadowStyle.subtle`, `.card`, `.elevated`,
  `.floating`, or the named text/card variants for ordinary surface and overlay
  shadows. Keep local `.shadow(...)` only for effect-specific glows, tinted
  recording affordances, or other intentionally colored rendering.
- Prefer `.sensoryFeedback` for haptics. Name trigger values by the user-visible
  event, such as `markReadFeedbackTrigger` or `saveCompleteFeedbackTrigger`.
- Respect `accessibilityReduceMotion` for decorative movement, staggered
  entrances, and long-running pulses.

## Type And Accessibility

- Use App Text Size for chrome, settings, controls, and navigation-heavy
  surfaces. Use Content Text Size for reading surfaces: Long Read, Fast Read,
  content detail, and chat routes.
- Apply content text scaling at route or tab boundaries with
  `.dynamicTypeSize(contentTextSize)` so rows, cards, and subviews inherit one
  consistent value instead of each reading settings directly.

## Validation

- Build and test iOS changes with XcodeBuildMCP when possible.
- Keep focused XCTest coverage close to behavior changes. For shared Swift
  conventions or docs-only updates, run the existing docs/client contract checks
  when those files changed.
