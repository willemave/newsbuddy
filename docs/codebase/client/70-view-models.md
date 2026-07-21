# client/newsly/newsly/ViewModels/

Source folder: `client/newsly/newsly/ViewModels`

## Purpose
Observable view-state layer that coordinates services, repositories, navigation state, pagination, background polling, and user actions for SwiftUI screens.

## Runtime behavior
- List view models manage cursor pagination, filters, loading/error state, and content/news refresh behavior.
- Non-briefing view models and app stores use Swift Observation (`@Observable`) and are owned with `@State` in SwiftUI. Briefing remains on the legacy `ObservableObject` path for `BriefingViewModel` and `BriefingDigViewModel` during the separate briefing initiative; the extracted `BriefingNarrationController` uses Swift Observation.
- Detail/chat view models coordinate repositories/services, action sheets, background message polling, timeline reconciliation, and navigation; chat history and auth are factory-injected through `RootDependencyFactory`, and detail screen dependencies use the same composition root; detail share markdown is built by the pure `ShareMarkdownBuilder`, link-add button state lives in `LinkSubmissionCoordinator`, and detail-local chat/discussion/podcast orchestration lives in `DetailChatCoordinator`, `DiscussionSheetCoordinator`, and `PodcastAudioController`. `ActivityViewPresenter` retries share-sheet presentation from UIKit transition completion instead of fixed async delays.
- Feature view models handle Learning chat history/composition, onboarding, custom narrations, Learning Decks, source settings, search, submissions, and tab coordination.
- `AuthenticationViewModel` takes auth and token-store dependencies through its initializer; the live singleton wiring lives in `RootDependencyFactory`.
- `ContentListViewModel` and `LongContentListViewModel` take their content-list service through initializers; list surface singletons stay in `RootDependencyFactory`.
- `ContentDetailViewModel` takes content, detected-feed, and toast dependencies through its initializer; `ContentDetailView` uses `RootDependencyFactory`, `LinkSubmissionCoordinator` receives a toast presenter instead of reading global toast state, and detail-local coordinators take chat, discussion, audio, navigation, and toast dependencies through initializers.
- `ScraperSettingsViewModel` takes its source-settings service through its initializer; source/settings screens use `RootDependencyFactory` for live wiring.
- `NewsGroupViewModel` takes unread-count and toast dependencies through its initializer; custom narration and submission-status view models are factory-wired or test-injected instead of reading services or toasts from their bodies.
- onboarding, discovery personalization, and Learning Deck view models are factory-wired for live services, stores, dictation, and deck/chat dependencies.
- ChatSessionViewModel and TweetSuggestionsViewModel receive auth, token, and transcription availability dependencies through factory wiring rather than reading auth/OpenAI/app settings singletons directly. `ChatSessionViewModel` delegates microphone/transcription state to `ChatVoiceInputController`, then submits completed transcripts through the same durable send pipeline as typed messages.
- `TaskBag` is the shared cancel-and-replace helper for view-model-owned async tasks such as foreground sends, debounce work, lazy lens loads, and polling loops. Learning Deck polling uses `TaskBag` so deck regeneration, deletion, and view-model teardown cancel outstanding refresh loops.
- `ShortNewsListViewModel` scroll-read batching uses an async debounce task; content/read repositories expose async methods directly, so app view models no longer use Combine publisher bridges.
- `ShortNewsListViewModel` projects contiguous day groups for `ShortFormView`, keeping day-delimiter structure out of the SwiftUI row loop.
- voice dictation events are consumed through `VoiceDictationCoordinator`, which adapts `SpeechTranscribing` events as an `AsyncStream` for chat, Knowledge, onboarding, discovery personalization, and Learning Deck focus recording.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Auth and root navigation | `AuthenticationViewModel.swift`, `TabCoordinatorViewModel.swift` | Observation-backed login/profile state and tab routing. |
| Lists and pagination | `PaginatedFeed.swift`, `ContentFeedSupport.swift`, `ContentListViewModel.swift`, `LongContentListViewModel.swift`, `ShortNewsListViewModel.swift`, `NewsGroupViewModel.swift`, `SubmissionStatusViewModel.swift` | Shared pagination plus content/news/submission list state; read projection comes from `ReadStateCache`. |
| Briefing | `BriefingViewModel.swift`, `BriefingNarrationController.swift`, `BriefingDigViewModel.swift` | Briefing index/lens cache, ETag refresh, lazy neighbor prefetch, read batching, per-lens narration preparation and playback, and two-stage dig-deeper state. |
| Details and readers | `ContentDetailViewModel.swift`, `ActivityViewPresenter.swift`, `ShareMarkdownBuilder.swift`, `LinkSubmissionCoordinator.swift`, `DetailChatCoordinator.swift`, `DiscussionSheetCoordinator.swift`, `PodcastAudioController.swift`, `TweetSuggestionsViewModel.swift` | Detail screens, UIKit share presentation, share markdown formatting, linked-article submission state, detail-local orchestration, and tweet suggestions. |
| Chat | `ChatSessionViewModel.swift`, `ChatVoiceInputController.swift`, `ChatSessionsViewModel.swift`, `ChatTimelineReconciler.swift`, `VoiceDictationCoordinator.swift` | Chat session/message lifecycle, focused voice-capture state, render timeline reconciliation, and shared dictation event handling. |
| Learning/search/discovery | `LearningHubViewModel.swift`, `SearchViewModel.swift`, `DiscoveryPersonalizeViewModel.swift` | Learning chat history/composition, search, and discovery-personalization state. |
| Onboarding | `OnboardingViewModel.swift` | Onboarding flow state. |
| Audio and Learning Decks | `CustomNarrationCreationViewModel.swift`, `CustomNarrationLibraryViewModel.swift`, `LearningDecksViewModel.swift` | Custom narration and Learning Deck sheets/lists. |
| Sources/submissions/settings | `ScraperSettingsViewModel.swift`, `SubmissionStatusViewModel.swift` | Source settings and submission status state. |

## Integration points
- View models should call services/repositories rather than constructing API requests in views.
- `RootDependencyFactory` in `Shared/AppChrome.swift` is the live composition root for shared app VMs such as auth, chat history, content lists, source settings, tab coordination, Knowledge, and Search.
- Tests under `client/newsly/newslyTests` cover many state transitions.
