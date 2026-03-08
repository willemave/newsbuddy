# client/newsly/newsly/Models/

Source folder: `client/newsly/newsly/Models`

## Purpose
Typed client-side models for API payloads, navigation routes, summaries, content metadata, discovery results, chat, onboarding, and live voice.

## Runtime behavior
- Mirrors the backend DTO layer so services and view models can decode stable Swift types instead of working with raw dictionaries.
- Captures client-only routing and presentation models such as detail routes, read filters, and chat model provider selection.

## Inventory scope
- Direct file inventory for `client/newsly/newsly/Models`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/Models/AnyCodable.swift` | `struct AnyCodable`, `encode` | Types: `struct AnyCodable`. Functions: `encode` |
| `client/newsly/newsly/Models/ArticleMetadata.swift` | `struct ArticleMetadata`, `enum CodingKeys`, `struct DateParser`, `encode`, `parse` | Types: `struct ArticleMetadata`, `enum CodingKeys`, `struct DateParser`. Functions: `encode`, `parse` |
| `client/newsly/newsly/Models/ChatMessage.swift` | `enum ChatMessageRole`, `enum ChatMessageDisplayType`, `enum MessageProcessingStatus`, `struct ChatMessage`, `enum CodingKeys` | Role of a chat message sender |
| `client/newsly/newsly/Models/ChatModelProvider.swift` | `enum ChatModelProvider` | Available LLM providers for chat sessions |
| `client/newsly/newsly/Models/ChatSessionDetail.swift` | `struct ChatSessionDetail`, `struct CreateChatSessionResponse`, `struct SendChatMessageResponse`, `enum CodingKeys`, `struct StartDailyDigestChatResponse`, `enum CodingKeys`, `struct MessageStatusResponse`, `enum CodingKeys`, `struct InitialSuggestionsResponse`, `enum CodingKeys`, +3 more | Full chat session details with message history |
| `client/newsly/newsly/Models/ChatSessionRoute.swift` | `enum ChatSessionRouteMode`, `struct ChatSessionRoute` | Types: `enum ChatSessionRouteMode`, `struct ChatSessionRoute` |
| `client/newsly/newsly/Models/ChatSessionSummary.swift` | `struct ChatSessionSummary`, `enum CodingKeys`, `hash` | Summary of a chat session for list view |
| `client/newsly/newsly/Models/ContentDetail.swift` | `struct ContentDetail`, `enum CodingKeys` | Check if this content has an interleaved summary format |
| `client/newsly/newsly/Models/ContentDetailRoute.swift` | `struct ContentDetailRoute` | Types: `struct ContentDetailRoute` |
| `client/newsly/newsly/Models/ContentDiscussion.swift` | `struct ContentDiscussion`, `enum CodingKeys`, `struct DiscussionComment`, `enum CodingKeys`, `struct DiscussionGroup`, `struct DiscussionItem`, `struct DiscussionLink`, `enum CodingKeys` | Types: `struct ContentDiscussion`, `enum CodingKeys`, `struct DiscussionComment`, `enum CodingKeys`, `struct DiscussionGroup`, `struct DiscussionItem`, `struct DiscussionLink`, `enum CodingKeys` |
| `client/newsly/newsly/Models/ContentListResponse.swift` | `struct PaginationMetadata`, `enum CodingKeys`, `struct ContentListResponse`, `enum CodingKeys` | Types: `struct PaginationMetadata`, `enum CodingKeys`, `struct ContentListResponse`, `enum CodingKeys` |
| `client/newsly/newsly/Models/ContentStatus.swift` | `enum ContentStatus` | Types: `enum ContentStatus` |
| `client/newsly/newsly/Models/ContentSummary.swift` | `struct ContentSummary`, `struct TopComment`, `enum CodingKeys`, `encode`, `updating` | Discussion snippet for feed card preview |
| `client/newsly/newsly/Models/ContentType.swift` | `enum ContentType` | Types: `enum ContentType` |
| `client/newsly/newsly/Models/DailyNewsDigest.swift` | `struct DailyNewsDigest`, `enum CodingKeys`, `struct DailyNewsDigestListResponse`, `struct DailyNewsDigestVoiceSummaryResponse`, `enum CodingKeys` | Types: `struct DailyNewsDigest`, `enum CodingKeys`, `struct DailyNewsDigestListResponse`, `struct DailyNewsDigestVoiceSummaryResponse`, `enum CodingKeys` |
| `client/newsly/newsly/Models/DetectedFeed.swift` | `struct DetectedFeed` | A detected RSS/Atom feed from a content page. |
| `client/newsly/newsly/Models/DiscoverySuggestion.swift` | `struct DiscoverySuggestion`, `enum CodingKeys`, `struct DiscoverySuggestionsResponse`, `enum CodingKeys`, `struct DiscoveryRunSuggestions`, `enum CodingKeys`, `struct DiscoveryHistoryResponse`, `struct DiscoveryRefreshResponse`, `enum CodingKeys`, `struct DiscoveryActionError`, +6 more | Types: `struct DiscoverySuggestion`, `enum CodingKeys`, `struct DiscoverySuggestionsResponse`, `enum CodingKeys`, `struct DiscoveryRunSuggestions`, `enum CodingKeys`, `struct DiscoveryHistoryResponse`, `struct DiscoveryRefreshResponse`. +8 more |
| `client/newsly/newsly/Models/LiveVoiceRoute.swift` | `enum LiveLaunchMode`, `enum LiveVoiceSourceSurface`, `struct LiveVoiceRoute` | Types: `enum LiveLaunchMode`, `enum LiveVoiceSourceSurface`, `struct LiveVoiceRoute` |
| `client/newsly/newsly/Models/NewsGroup.swift` | `enum NewsRowTypography`, `enum NewsRowLayout`, `struct NewsGroup`, `calculateOptimalGroupSize`, `estimateRowHeight`, `estimatedRowHeight`, `grouped`, `groupedBySeven`, `groupedToFit`, `projected`, +2 more | Represents a dynamically-sized group of news items displayed together |
| `client/newsly/newsly/Models/NewsMetadata.swift` | `struct NewsSummaryMetadata`, `enum CodingKeys`, `struct NewsArticleMetadata`, `enum CodingKeys`, `struct NewsAggregatorMetadata`, `enum CodingKeys`, `struct NewsMetadata`, `enum CodingKeys`, `struct NewsRelatedLink` | Types: `struct NewsSummaryMetadata`, `enum CodingKeys`, `struct NewsArticleMetadata`, `enum CodingKeys`, `struct NewsAggregatorMetadata`, `enum CodingKeys`, `struct NewsMetadata`, `enum CodingKeys`. +1 more |
| `client/newsly/newsly/Models/Onboarding.swift` | `struct OnboardingProfileRequest`, `enum CodingKeys`, `struct OnboardingProfileResponse`, `enum CodingKeys`, `struct OnboardingVoiceParseRequest`, `struct OnboardingVoiceParseResponse`, `enum CodingKeys`, `struct OnboardingAudioDiscoverRequest`, `struct OnboardingDiscoveryLaneStatus`, `enum CodingKeys`, +18 more | Types: `struct OnboardingProfileRequest`, `enum CodingKeys`, `struct OnboardingProfileResponse`, `enum CodingKeys`, `struct OnboardingVoiceParseRequest`, `struct OnboardingVoiceParseResponse`, `enum CodingKeys`, `struct OnboardingAudioDiscoverRequest`. +20 more |
| `client/newsly/newsly/Models/OpenAI.swift` | `struct RealtimeTokenResponse`, `enum CodingKeys`, `struct AudioTranscriptionResponse`, `enum CodingKeys`, `encode` | Types: `struct RealtimeTokenResponse`, `enum CodingKeys`, `struct AudioTranscriptionResponse`, `enum CodingKeys`. Functions: `encode` |
| `client/newsly/newsly/Models/PodcastMetadata.swift` | `struct PodcastMetadata`, `enum CodingKeys`, `encode` | Types: `struct PodcastMetadata`, `enum CodingKeys`. Functions: `encode` |
| `client/newsly/newsly/Models/ReadFilter.swift` | `enum ReadFilter` | Types: `enum ReadFilter` |
| `client/newsly/newsly/Models/ScraperConfig.swift` | `struct ScraperConfig`, `enum CodingKeys` | Types: `struct ScraperConfig`, `enum CodingKeys` |
| `client/newsly/newsly/Models/StructuredSummary.swift` | `struct StructuredSummary`, `enum CodingKeys`, `struct BulletPoint`, `struct Quote`, `enum CodingKeys`, `struct InterleavedInsight`, `enum CodingKeys`, `struct InterleavedSummary`, `enum CodingKeys`, `struct InterleavedTopic`, +8 more | Types: `struct StructuredSummary`, `enum CodingKeys`, `struct BulletPoint`, `struct Quote`, `enum CodingKeys`, `struct InterleavedInsight`, `enum CodingKeys`, `struct InterleavedSummary`. +10 more |
| `client/newsly/newsly/Models/SubmissionStatusItem.swift` | `struct SubmissionStatusItem`, `enum CodingKeys`, `struct SubmissionStatusListResponse`, `enum CodingKeys` | Types: `struct SubmissionStatusItem`, `enum CodingKeys`, `struct SubmissionStatusListResponse`, `enum CodingKeys` |
| `client/newsly/newsly/Models/TweetSuggestion.swift` | `struct TweetSuggestion`, `enum CodingKeys`, `struct TweetSuggestionsRequest`, `enum CodingKeys`, `struct TweetSuggestionsResponse`, `enum CodingKeys` | A single tweet suggestion from the LLM. |
| `client/newsly/newsly/Models/User.swift` | `struct User`, `enum CodingKeys`, `struct TokenResponse`, `enum CodingKeys`, `struct AuthSession`, `struct RefreshTokenRequest`, `enum CodingKeys`, `struct AccessTokenResponse`, `enum CodingKeys`, `struct UpdateUserProfileRequest`, +1 more | User account model matching backend UserResponse schema |
| `client/newsly/newsly/Models/VoiceLive.swift` | `struct VoiceCreateSessionRequest`, `enum CodingKeys`, `struct VoiceCreateSessionResponse`, `enum CodingKeys`, `struct VoiceServerEvent`, `enum CodingKeys` | Types: `struct VoiceCreateSessionRequest`, `enum CodingKeys`, `struct VoiceCreateSessionResponse`, `enum CodingKeys`, `struct VoiceServerEvent`, `enum CodingKeys` |
