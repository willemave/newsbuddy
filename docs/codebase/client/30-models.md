# client/newsly/newsly/Models/

Source folder: `client/newsly/newsly/Models`

## Purpose
Swift model layer for API payloads, local route state, feature state, metadata, and presentation-specific value objects.

## Runtime behavior
- Models mirror backend DTOs where the app still uses hand-written types. Learning Deck wire
  decoding uses generated API types and `LearningDeck+API.swift` maps them into domain models.
- Route models drive navigation for content detail, chat sessions, saved Knowledge search, and session history.
- Metadata models decode backend JSON fields for article, podcast, news, source, long-form artifact, and structured summary surfaces.
- Generated enum/contract types live in `Models/Generated` and are documented separately.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Content and metadata | `ContentSummary.swift`, `ContentDetail.swift`, `ContentBody.swift`, `ContentListResponse.swift`, `ContentStatus.swift`, `ContentType.swift`, `ArticleMetadata.swift`, `PodcastMetadata.swift`, `NewsMetadata.swift`, `SourceMetadata.swift`, `LongformArtifact.swift`, `StructuredSummary.swift` | Content/news payloads and metadata contracts. |
| News/search/source config | `MixedSearchResponse.swift`, `DetectedFeed.swift`, `ScraperConfig.swift` | Fast Reads, search, detected feeds, and source config models. |
| Chat | `ChatMessage.swift`, `ChatModelProvider.swift`, `ChatSessionDetail.swift`, `ChatSessionSummary.swift`, `ChatSessionRoute.swift`, `ChatTimelineItem.swift`, `SessionHistoryRoute.swift` | Chat sessions, messages, timeline rendering, and navigation. |
| Knowledge/routes | `ContentDetailRoute.swift` | Saved-content detail route state. |
| Learning/audio | `LearningDeck.swift`, `LearningDeck+API.swift`, `Narration.swift`, `TweetSuggestion.swift` | Learning Deck domain behavior and generated-contract mapping, narration/audio, and tweet suggestion payloads. |
| Auth/onboarding/users | `User.swift`, `Onboarding.swift`, `OpenAI.swift`, `SubmissionStatusItem.swift`, `AnyCodable.swift` | User/auth-adjacent models, onboarding, OpenAI transcription status, submission status, and flexible JSON values. |

## Integration points
- Services decode hand-written or generated wire models from `APIClient`, then map wire-only
  representations at the service boundary where required.
- View models own most state mutations and expose these values to SwiftUI views.
