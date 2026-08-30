# iOS Coding Guidelines

These conventions apply to the SwiftUI app and share extension under
`client/newsly`. Keep durable product behavior in `docs/laws/`, system design in
`docs/architecture.md`, and Swift implementation patterns in this file.

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
  `AuthenticatedSession` owns user-scoped stores. `RootDependencyFactory` is a
  transitional bridge for unmigrated route construction; do not add user-scoped
  lifetime or service-locator behavior to it.

## Async State, Lifecycle, And Work Ownership

Use `LoadPhase` for paginated or collection-shaped UI state:

```swift
enum LoadPhase: Equatable {
    case idle
    case initialLoading
    case empty
    case loaded
    case loadingMore
    case error(String)
}
```

Keep pagination truth separate from presentation state. The cursor, `hasMore`,
request generation, replacement merge, and append rules belong in
`PaginatedFeed`, not in the view. Convert `Error` values to display strings only
at the presentation boundary.

A non-paginated read that can retain a value needs independent state rather than
a single loading enum:

- the readable value and its typed resource key;
- an initial-load phase used only while no readable value exists;
- a separate revalidation activity or failure while a readable value remains.

An initial failure may replace an empty loading surface with a blocking retry.
A revalidation failure keeps the current value and, when useful, reports a
nonblocking message. Cancellation or key replacement restores the prior phase
and never produces an error. Do not force Content Detail, Briefing, or another
retained-value screen into `LoadPhase` when that would conflate value presence
with request activity.

Use `TaskBag` for view-model-owned work. Key each ordinary read by resource
identity, coalesce callers for the same key, cancel and replace a different key,
and fence both success and failure with the request generation. Commit dependent
effects such as tracking, read marks, or body loading once inside the winning
generation; every caller awaiting the coalesced task must not repeat them. Keep
request-start mutation revisions and feature reconciliation with the feature.
Pass the winning token into secondary reads and check it before publishing even
when the current transport normally cooperates with cancellation. When several
callers await one task, cancellation belongs to the task owner rather than an
arbitrary waiter. In particular, a lifecycle waiter must not cancel a shared
read after an explicit user refresh has promoted it to continue independently.

`AppLifecycle.activation.generation` identifies initial activation and true warm
resume. Visible screens may use it as a `.task(id:)` input to decide whether a
resource is stale. It does not replace a feature request generation, and an
inactive-to-active interruption without background does not start routine
revalidation. On true background, suspend obsolete automatic reads and pollers;
on activation, resume only visible or process-owned work under its existing
coalescing rules.

Name server work by what it does:

- A **load** obtains the first readable value.
- A **revalidation** reads server state while retaining the current value.
- A **command** submits a mutation once. Generic transport policy must not retry
  it after an ambiguous failure.
- An **observation** polls or subscribes after the command returned a durable
  identity. Backgrounding may pause observation, and foregrounding resumes from
  that identity without resending the command.

When touching legacy surfaces, migrate boolean loading/error pairs and local
list phases toward these rules. Keep specialized state machines for Briefing,
chat, Learning Deck generation, audio, and WebKit when their domain phases do not
fit an ordinary read.

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
