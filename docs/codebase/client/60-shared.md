# client/newsly/newsly/Shared/

Source folder: `client/newsly/newsly/Shared`

## Purpose
Shared observable state and container helpers reused across tabs, detail flows, onboarding, and the share extension.

## Runtime behavior
- Persists or coordinates cross-view state such as reading restoration, chat scroll position, and onboarding progress.
- Holds shared app-group/container helpers used to communicate with the extension and shared storage.

## Inventory scope
- Direct file inventory for `client/newsly/newsly/Shared`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/Shared/ChatScrollStateStore.swift` | `struct ChatScrollState`, `enum ChatScrollStateStore`, `clear`, `load`, `save` | Types: `struct ChatScrollState`, `enum ChatScrollStateStore`. Functions: `clear`, `load`, `save` |
| `client/newsly/newsly/Shared/OnboardingStateStore.swift` | `class OnboardingStateStore`, `clearDiscoveryRun`, `discoveryRunId`, `setDiscoveryRun` | Types: `class OnboardingStateStore`. Functions: `clearDiscoveryRun`, `discoveryRunId`, `setDiscoveryRun` |
| `client/newsly/newsly/Shared/ReadingStateStore.swift` | `struct ReadingState`, `class ReadingStateStore`, `clear`, `markAsRead`, `setCurrent` | Notification posted when content is marked as read from detail view |
| `client/newsly/newsly/Shared/SharedContainer.swift` | `enum SharedContainer`, `enum ShareURLHandlerKind`, `struct ShareURLHandlerMatch`, `enum ShareURLRouting`, `extractURLs`, `handler`, `preferredURL`, `rank` | App group identifier shared between the main app and extensions |
