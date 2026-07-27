# client/newsly/newsly/Views/

Source folder: `client/newsly/newsly/Views`

## Purpose
Top-level SwiftUI screens, routed feature surfaces, and modal sheets. Subfolders document reusable components, onboarding, settings, shared primitives, sources, library, and chat-specific subviews.

## Runtime behavior
- `AuthenticatedRootView` owns the main authenticated tab/root surface.
- `RootTabs.swift` contains the per-tab root `NavigationStack` views; `ContentRoutes.swift` centralizes shared tab destinations.
- Briefing, Knowledge, Learning, search, recently-read, submissions, settings/more, and detail screens bind to view models and services.
- Briefing is the only reading composition root; Knowledge owns its saved-row presentation and Learning owns the merged chat/deck/narration timeline.
- Saved-library, recently-read, and submissions lists use the shared `onPaginationThresholdReached` scroll-depth modifier instead of manual or last-row pagination triggers.
- Learning chat rows and article-reader entrypoints use the shared `ContentZoomTransition` helper so chat-row-to-chat and detail-to-reader presentations use iOS 18 zoom transitions through `ContentRoutes` or the reader cover.
- Detail edge-swipe drag and snapback use the shared `AppMotion.press` token with sensory-feedback triggers instead of bespoke springs/manual haptics.
- `LandingView` keeps the animated mascot/title treatment for normal motion settings and switches to a static title treatment when Reduce Motion is enabled.
- Chat, onboarding, settings, shared, source, library, and component subfolders keep specialized UI out of the top-level screen files.
- Reader/narration/learning/chat sheets are presented from feature surfaces rather than landing pages.

## Important files
| File | Purpose |
|---|---|
| `AuthenticatedRootView.swift` | Main authenticated app root/tab surface. |
| `ContentRoutes.swift`, `RootTabs.swift` | Shared root-tab navigation stacks and destinations. |
| `ContentZoomTransition.swift` | Shared matched-source and destination zoom-transition modifiers for content routes. |
| `LandingView.swift` | Unauthenticated landing surface. |
| `Briefing/BriefingView.swift` | Native Briefing tab surface with lens paging, source sheets, dig panel, and narration controls. |
| `ContentDetailView.swift`, `ArticleReaderView.swift` | Detail and reader surfaces. |
| `KnowledgeView.swift`, `LearningView.swift`, `RecentlyReadView.swift`, `SearchView.swift` | Saved Knowledge, merged Learning activity, search, and reading-history screens. |
| `ChatSessionView.swift`, `ChatSessionHistoryView.swift` | Chat session shell and history screen. |
| `DiscoveryPersonalizeSheet.swift` | Discovery personalization sheet. |
| `ProcessingStatsView.swift` | Processing count/status surface. |
| `SubmissionDetailView.swift`, `SubmissionsView.swift` | Submitted URL status/detail screens. |
| `MoreView.swift`, `DebugMenuView.swift` | More/settings/debug entrypoints. |

## Subfolders
| Folder | Doc |
|---|---|
| `Components/` | `81-views-components.md` |
| `Onboarding/` | `82-views-onboarding.md` |
| `Settings/` | `83-views-settings.md` |
| `Shared/` | `84-views-shared.md` |
| `Sources/` | `85-views-sources.md` |
| `Chat/` | `87-views-chat.md` |
| `Briefing/` | `88-views-briefing.md` |
