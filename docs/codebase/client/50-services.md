# client/newsly/newsly/Services/

Source folder: `client/newsly/newsly/Services`

## Purpose
App service layer for network transport, auth, content/news APIs, chat, discovery, onboarding, audio episodes, Learning Decks, CLI link, feedback, dictation/transcription, images, notifications, settings, and integrations.

## Runtime behavior
- `APIClient` owns HTTP transport, auth headers, token refresh behavior, raw/typed requests, void requests, and streamed NDJSON publishing.
- `APIEndpoints` centralizes backend endpoint paths.
- Feature services wrap `APIClient` for content, chat, discovery, onboarding, scraper configs, audio episodes, Learning Decks, feedback, OpenAI transcription, and X integration.
- Voice capture is now dictation/transcription-oriented: `SpeechTranscribing` routes through `VoiceDictationService` and `OpenAIService`; removed live websocket/capture/playback services are no longer present.
- Local device services cover keychain storage, image caching/prefetching, notifications, app settings, toasts, narration playback, and Twitter/X sharing.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Transport and endpoints | `APIClient.swift`, `APIEndpoints.swift` | HTTP transport, auth handling, endpoint construction, streaming. |
| Auth and local storage | `AuthenticationService.swift`, `KeychainManager.swift`, `AppSettings.swift`, `OnboardingE2EFixtureStore.swift` | Sign-in, tokens, preferences, and E2E onboarding fixture state. |
| Content/news/source APIs | `ContentService.swift`, `ScraperConfigService.swift`, `ProcessingCountService.swift`, `UnreadCountService.swift`, `ContentImagePrefetcher.swift` | Content/news lists/details/actions, source config, counts, and image prefetching. |
| Chat | `ChatService.swift`, `ActiveChatSessionManager.swift`, `ChatNavigationCoordinator.swift`, `ChatGPTDeepLinkService.swift` | Chat sessions/messages, background active-session state, navigation, and ChatGPT deep links. |
| Discovery/onboarding | `DiscoveryService.swift`, `OnboardingService.swift` | Discovery suggestions/history/actions and onboarding API calls. |
| Learning and audio | `LearningDeckService.swift`, `AudioEpisodeService.swift`, `NarrationPlaybackService.swift` | Learning Deck CRUD/share/viewer URLs, audio episode APIs, and local narration playback. |
| Voice/transcription | `SpeechTranscribing.swift`, `VoiceDictationService.swift`, `OpenAIService.swift`, `VoiceFFTAnalyzer.swift` | Dictation state, audio upload transcription, microphone capture, and waveform energy. |
| UI/device utilities | `ImageCacheService.swift`, `LocalNotificationService.swift`, `ToastService.swift`, `TwitterShareService.swift` | Image cache, notifications, toast state, and X/Twitter sharing. |
| Integrations | `XIntegrationService.swift`, `CLILinkService.swift`, `FeedbackService.swift` | X OAuth, CLI QR link approval, and user feedback submission. |

## Integration points
- View models own user-facing state and call services/repositories.
- Models under `client/newsly/newsly/Models` define request/response payloads.
- Backend route changes should update `APIEndpoints`, services, models, and generated contracts together.
