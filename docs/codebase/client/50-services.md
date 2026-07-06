# client/newsly/newsly/Services/

Source folder: `client/newsly/newsly/Services`

## Purpose
App service layer for network transport, auth, content/news APIs, chat, onboarding, audio episodes, Learning Decks, CLI link, feedback, dictation/transcription, images, notifications, settings, and integrations.

## Runtime behavior
- `APIClient` owns HTTP transport, auth headers, token refresh behavior, raw/typed async requests, void requests, and streamed NDJSON publishing.
- Network services use the configured `URLSession.newslyDefault` timeout profile through `APIClient` or direct service calls.
- `TokenRefreshService` is the shared refresh path; services that need an access token depend on the `TokenRefreshing` abstraction instead of reimplementing refresh logic.
- `AuthenticationService` uses a single ISO-8601 auth response decoder factory for user/session payloads, avoiding repeated per-call date-decoder setup without sharing mutable decoder instances across async requests.
- `APIClient` posts `.authenticationRequired` only as an auth-state signal; `AuthenticationViewModel` is its direct observer and downstream services reset from `.authDidLogOut`.
- `APIEndpoints` centralizes backend endpoint paths.
- Feature services wrap `APIClient` for content, chat, onboarding, scraper configs, audio episodes, Learning Decks, feedback, OpenAI transcription, and X integration.
- Voice capture is dictation/transcription-oriented: `SpeechTranscribing` routes through `VoiceDictationService` and `OpenAIService`, and exposes transcription state/transcript/error/stop-reason updates as an `AsyncStream` consumed by view-model coordinators. The service owns audio/session/transcription state only; mic haptics are SwiftUI `.sensoryFeedback` on `TapToTalkMicButton`.
- Local device services cover keychain storage, image caching/prefetching, notifications, app settings, toasts, narration playback, and Twitter/X sharing. `ImageCacheService` keeps a two-tier memory/disk cache, downsampling for requested target sizes, logs cache write/download failures, and runs throttled disk cleanup after launch and later writes. `ActiveChatSessionManager` preserves active chat state across navigation, and `BadgeStatsRefreshCoordinator` retries active processing badge refreshes, but both polling paths are suspended while the app scene is inactive or backgrounded.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Transport and endpoints | `APIClient.swift`, `APIEndpoints.swift` | HTTP transport, configured URLSession timeouts, auth handling, endpoint construction, streaming. |
| Auth and local storage | `AuthenticationService.swift`, `AuthError.swift`, `KeychainManager.swift`, `TokenRefreshService.swift`, `AppSettings.swift`, `OnboardingE2EFixtureStore.swift` | Sign-in, auth error presentation, locked token access-group storage, token refresh, preferences, and E2E onboarding fixture state. |
| Content/news/source APIs | `ContentService.swift`, `ScraperConfigService.swift`, `ProcessingCountService.swift`, `UnreadCountService.swift`, `ContentImagePrefetcher.swift` | Content/news lists/details/actions, source config, counts, and image prefetching. |
| Briefing | `BriefingService.swift` | ETag-backed Briefing index, lazy lens fetches, read marks, manual refresh, dig-deeper calls, and narration creation. |
| Chat | `ChatService.swift`, `ActiveChatSessionManager.swift`, `ChatNavigationCoordinator.swift` | Chat sessions/messages, lifecycle-gated active-session polling state, and navigation. |
| Onboarding | `OnboardingService.swift` | Onboarding profile, fast-discover, audio-discovery, and status API calls. |
| Learning and audio | `LearningDeckService.swift`, `AudioEpisodeService.swift`, `NarrationPlaybackService.swift` | Learning Deck CRUD/share/viewer URLs, audio episode APIs, and local narration playback. |
| Voice/transcription | `SpeechTranscribing.swift`, `VoiceDictationService.swift`, `OpenAIService.swift` | Dictation event stream, audio upload transcription, and microphone capture. |
| UI/device utilities | `ImageCacheService.swift`, `LocalNotificationService.swift`, `ToastService.swift`, `TwitterShareService.swift` | Image cache, notifications, toast state, and X/Twitter sharing. |
| Integrations | `XIntegrationService.swift`, `CLILinkService.swift`, `FeedbackService.swift` | X OAuth, CLI QR link approval, and user feedback submission. |

## Integration points
- View models own user-facing state and call services/repositories.
- Models under `client/newsly/newsly/Models` define request/response payloads.
- Backend route changes should update `APIEndpoints`, services, models, and generated contracts together.
