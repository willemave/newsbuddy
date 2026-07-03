# client/newsly/newsly/Views/

Source folder: `client/newsly/newsly/Views`

## Purpose
Top-level SwiftUI screens, routed feature surfaces, and modal sheets. Subfolders document reusable components, onboarding, settings, shared primitives, sources, library, and chat-specific subviews.

## Runtime behavior
- `AuthenticatedRootView` owns the main authenticated tab/root surface.
- Long-form, short-form, Briefing, Knowledge, search, recently-read, submissions, settings/more, and detail screens bind to view models and services.
- Chat, onboarding, settings, shared, source, library, and component subfolders keep specialized UI out of the top-level screen files.
- Reader/narration/learning/chat sheets are presented from feature surfaces rather than landing pages.

## Important files
| File | Purpose |
|---|---|
| `AuthenticatedRootView.swift` | Main authenticated app root/tab surface. |
| `AuthenticationView.swift`, `LandingView.swift` | Login/landing surfaces. |
| `LongFormView.swift`, `ShortFormView.swift`, `ContentListView.swift` | Primary content list surfaces. |
| `Briefing/BriefingView.swift` | Native Briefing tab surface with lens paging, source sheets, dig panel, and narration controls. |
| `ContentDetailView.swift`, `ArticleReaderView.swift`, `LongFormActionsView.swift` | Detail/reader/action surfaces. |
| `KnowledgeView.swift`, `KnowledgeDiscoveryView.swift`, `RecentlyReadView.swift`, `SearchView.swift` | Knowledge, discovery, search, and reading-history screens. |
| `ChatSessionView.swift`, `ChatSessionHistoryView.swift` | Chat session shell and history screen. |
| `CustomNarrationListSheet.swift`, `CustomNarrationPickerSheet.swift` | Custom narration sheets. |
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
| `Library/` | `86-views-library.md` |
| `Chat/` | `87-views-chat.md` |
| `Briefing/` | `88-views-briefing.md` |
