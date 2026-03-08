# client/newsly/newslyTests/

Source folder: `client/newsly/newslyTests`

## Purpose
Focused iOS unit tests covering share routing, onboarding animation progress, and daily-digest dig-deeper behavior.

## Runtime behavior
- Provides regression coverage for high-risk client-side behaviors that do not require full UI tests.

## Inventory scope
- Direct file inventory for `client/newsly/newslyTests`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newslyTests/AncientScrollRevealProgressTests.swift` | `class AncientScrollRevealProgressTests`, `testGlyphPhraseCyclerCanReturnSpacesWhenRequested`, `testGlyphPhraseCyclerSkipsSpacesAndCycles`, `testImpulseVectorIncludesDragVelocityInfluence`, `testImpulseVectorPushesAwayFromTouchPoint`, `testNormalizedImpulseFallsOffByDistance` | Types: `class AncientScrollRevealProgressTests`. Functions: `testGlyphPhraseCyclerCanReturnSpacesWhenRequested`, `testGlyphPhraseCyclerSkipsSpacesAndCycles`, `testImpulseVectorIncludesDragVelocityInfluence`, `testImpulseVectorPushesAwayFromTouchPoint`, `testNormalizedImpulseFallsOffByDistance` |
| `client/newsly/newslyTests/ChatMessageDisplayTests.swift` | `class ChatMessageDisplayTests`, `testChatMessageDecodesProcessSummaryDisplayMetadata`, `testChatSessionDetailPreservesProcessSummaryOrdering` | Types: `class ChatMessageDisplayTests`. Functions: `testChatMessageDecodesProcessSummaryDisplayMetadata`, `testChatSessionDetailPreservesProcessSummaryOrdering` |
| `client/newsly/newslyTests/DailyDigestDigDeeperTests.swift` | `class DailyDigestDigDeeperTests`, `fetchVoiceSummary`, `fetchVoiceSummaryAudio`, `loadPage`, `markRead`, `markUnread`, `startDigDeeperChat`, `testChatSessionSummaryUsesDailyDigestPresentation`, `testDailyDigestListViewModelStartsDigDeeperChatAndTracksLoading`, `testDailyDigestListViewModelStoresDigDeeperErrorPerDigest`, +1 more | Types: `class DailyDigestDigDeeperTests`. Functions: `fetchVoiceSummary`, `fetchVoiceSummaryAudio`, `loadPage`, `markRead`, `markUnread`, `startDigDeeperChat`, `testChatSessionSummaryUsesDailyDigestPresentation`, `testDailyDigestListViewModelStartsDigDeeperChatAndTracksLoading`. +2 more |
| `client/newsly/newslyTests/ShareURLRoutingTests.swift` | `class ShareURLRoutingTests`, `testApplePodcastDetectedAsApplePodcastShare`, `testExtractURLsDedupesAndKeepsOrder`, `testPreferredURLPrioritizesSingleVideoOverChannelURL`, `testSpotifyEpisodeDetectedAsPodcastPlatformShare`, `testYouTubeChannelDetectedAsGenericYouTubeShare`, `testYouTubeEmbedDetectedAsSingleVideoHandler`, `testYouTubeLegacyVDetectedAsSingleVideoHandler`, `testYouTubeLiveDetectedAsSingleVideoHandler`, `testYouTubeShortsDetectedAsSingleVideoHandler`, +2 more | Types: `class ShareURLRoutingTests`. Functions: `testApplePodcastDetectedAsApplePodcastShare`, `testExtractURLsDedupesAndKeepsOrder`, `testPreferredURLPrioritizesSingleVideoOverChannelURL`, `testSpotifyEpisodeDetectedAsPodcastPlatformShare`, `testYouTubeChannelDetectedAsGenericYouTubeShare`, `testYouTubeEmbedDetectedAsSingleVideoHandler`, `testYouTubeLegacyVDetectedAsSingleVideoHandler`, `testYouTubeLiveDetectedAsSingleVideoHandler`. +3 more |
