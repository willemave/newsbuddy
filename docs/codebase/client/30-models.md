# client/newsly/newsly/Models/

Source folder: `client/newsly/newsly/Models`

## Purpose
Swift model layer for API payloads, local route state, feature state, metadata, and presentation-specific value objects.

## Runtime behavior
- Models mirror backend DTOs where the app still uses hand-written types.
- Route models drive navigation for content detail, chat sessions, favorites/library, and session history.
- Metadata models decode backend JSON fields for article, podcast, news, source, long-form artifact, and structured summary surfaces.
- Generated enum/contract types live in `Models/Generated` and are documented separately.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Content and metadata | `ContentSummary.swift`, `ContentDetail.swift`, `ContentBody.swift`, `ContentListResponse.swift`, `ContentStatus.swift`, `ContentType.swift`, `ArticleMetadata.swift`, `PodcastMetadata.swift`, `NewsMetadata.swift`, `SourceMetadata.swift`, `LongformArtifact.swift`, `StructuredSummary.swift` | Content/news payloads and metadata contracts. |
| News/search/discovery | `NewsGroup.swift`, `MixedSearchResponse.swift`, `DiscoverySuggestion.swift`, `DetectedFeed.swift`, `ReadFilter.swift`, `ScraperConfig.swift` | Fast Reads, search, discovery, and source config models. |
| Chat | `ChatMessage.swift`, `ChatModelProvider.swift`, `ChatSessionDetail.swift`, `ChatSessionSummary.swift`, `ChatSessionRoute.swift`, `ChatTimelineItem.swift`, `SessionHistoryRoute.swift` | Chat sessions, messages, timeline rendering, and navigation. |
| Knowledge/library/routes | `ContentDetailRoute.swift`, `FavoritesRoute.swift` | Route and library state. |
| Learning/audio | `LearningDeck.swift`, `Narration.swift`, `TweetSuggestion.swift` | Learning Decks, narration/audio, and tweet suggestion payloads. |
| Auth/onboarding/users | `User.swift`, `Onboarding.swift`, `OpenAI.swift`, `SubmissionStatusItem.swift`, `AnyCodable.swift` | User/auth-adjacent models, onboarding, OpenAI transcription status, submission status, and flexible JSON values. |

## Integration points
- Services decode these models from `APIClient`.
- View models own most state mutations and expose these values to SwiftUI views.
