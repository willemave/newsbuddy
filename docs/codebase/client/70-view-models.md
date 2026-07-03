# client/newsly/newsly/ViewModels/

Source folder: `client/newsly/newsly/ViewModels`

## Purpose
Observable view-state layer that coordinates services, repositories, navigation state, pagination, background polling, and user actions for SwiftUI screens.

## Runtime behavior
- List view models manage cursor pagination, filters, loading/error state, and content/news refresh behavior.
- Detail/chat view models coordinate repositories/services, action sheets, background message polling, timeline reconciliation, and navigation.
- Feature view models handle Knowledge, discovery, onboarding, Quick Mic, custom narrations, Learning Decks, source settings, search, submissions, and tab coordination.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Auth and root navigation | `AuthenticationViewModel.swift`, `TabCoordinatorViewModel.swift` | Login/profile state and tab routing. |
| Lists and pagination | `BaseContentListViewModel.swift`, `CursorPaginatedViewModel.swift`, `ContentListViewModel.swift`, `LongContentListViewModel.swift`, `ShortNewsListViewModel.swift`, `NewsGroupViewModel.swift` | Content/news list state and pagination. |
| Briefing | `BriefingViewModel.swift`, `BriefingDigViewModel.swift` | Briefing index/lens cache, ETag refresh, lazy neighbor prefetch, read batching, narration episode state, and two-stage dig-deeper state. |
| Details and readers | `ArticleDetailViewModel.swift`, `ContentDetailViewModel.swift`, `PodcastDetailViewModel.swift`, `CardStackKeyPointsLoader.swift`, `TweetSuggestionsViewModel.swift` | Detail screens, cards, podcast state, and tweet suggestions. |
| Chat | `ChatSessionViewModel.swift`, `ChatSessionsViewModel.swift`, `ChatTimelineReconciler.swift` | Chat session/message lifecycle and render timeline reconciliation. |
| Knowledge/search/discovery | `KnowledgeHubViewModel.swift`, `SearchViewModel.swift`, `DiscoveryViewModel.swift`, `DiscoveryPersonalizeViewModel.swift` | Knowledge hub, search, and discovery state. |
| Onboarding and voice | `OnboardingViewModel.swift`, `QuickMicViewModel.swift` | Onboarding flow and Quick Mic dictation state. |
| Audio and Learning Decks | `CustomNarrationCreationViewModel.swift`, `CustomNarrationLibraryViewModel.swift`, `LearningDecksViewModel.swift` | Custom narration and Learning Deck sheets/lists. |
| Sources/submissions/settings | `ScraperSettingsViewModel.swift`, `SubmissionStatusViewModel.swift` | Source settings and submission status state. |

## Integration points
- View models should call services/repositories rather than constructing API requests in views.
- Tests under `client/newsly/newslyTests` cover many state transitions.
