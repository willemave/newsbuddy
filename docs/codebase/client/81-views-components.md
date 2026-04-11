# client/newsly/newsly/Views/Components/

Source folder: `client/newsly/newsly/Views/Components`

## Purpose
Reusable SwiftUI building blocks for cards, summaries, markdown rendering, filters, live voice states, discovery cards, toasts, and media presentation.

## Runtime behavior
- Holds composable UI pieces shared by multiple screens so detail and list views can stay thin.
- Contains summary renderers for interleaved, editorial, bulleted, and structured summary payloads returned by the backend.
- Packages complex UI atoms such as swipeable cards, async image wrappers, and live voice visual states.

## Inventory scope
- Direct file inventory for `client/newsly/newsly/Views/Components`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/Views/Components/ArticleCardView.swift` | `struct ArticleCardView` | Types: `struct ArticleCardView` |
| `client/newsly/newsly/Views/Components/BulletedSummaryView.swift` | `struct BulletedSummaryView` | Types: `struct BulletedSummaryView` |
| `client/newsly/newsly/Views/Components/CachedAsyncImage.swift` | `struct CachedAsyncImage` | A cached version of AsyncImage that uses ImageCacheService for memory and disk caching |
| `client/newsly/newsly/Views/Components/CardStackView.swift` | `struct CardStackView` | Types: `struct CardStackView` |
| `client/newsly/newsly/Views/Components/ChatLoadingView.swift` | `struct ChatLoadingView` | Types: `struct ChatLoadingView` |
| `client/newsly/newsly/Views/Components/ChatMarkdownTheme.swift` | n/a | A compact markdown theme optimized for chat bubbles |
| `client/newsly/newsly/Views/Components/ChatStatusBanner.swift` | `struct ChatStatusBanner`, `enum BannerStyle`, `applyBannerStyle` | A small banner that shows the status of an active chat session |
| `client/newsly/newsly/Views/Components/ContentCard.swift` | `struct ContentCard` | Build a full URL for the image, handling relative paths |
| `client/newsly/newsly/Views/Components/ContentTypeBadge.swift` | `struct ContentTypeBadge` | Types: `struct ContentTypeBadge` |
| `client/newsly/newsly/Views/Components/DetectedFeedCard.swift` | `struct DetectedFeedCard` | A card that shows when a feed is detected for the current content, allowing the user to subscribe to it. |
| `client/newsly/newsly/Views/Components/DiscoveryRunSection.swift` | `struct DiscoveryRunSection` | Types: `struct DiscoveryRunSection` |
| `client/newsly/newsly/Views/Components/DiscoveryStateViews.swift` | `struct DiscoveryLoadingStateView`, `struct DiscoveryErrorStateView`, `struct DiscoveryProcessingStateView`, `struct DiscoveryEmptyStateView` | Types: `struct DiscoveryLoadingStateView`, `struct DiscoveryErrorStateView`, `struct DiscoveryProcessingStateView`, `struct DiscoveryEmptyStateView` |
| `client/newsly/newsly/Views/Components/DiscoverySuggestionCard.swift` | `struct DiscoverySuggestionCard`, `struct EditorialCardButtonStyle`, `makeBody` | Types: `struct DiscoverySuggestionCard`, `struct EditorialCardButtonStyle`. Functions: `makeBody` |
| `client/newsly/newsly/Views/Components/DownloadMoreMenu.swift` | `struct DownloadMoreMenu` | Types: `struct DownloadMoreMenu` |
| `client/newsly/newsly/Views/Components/EditorialNarrativeSummaryView.swift` | `struct EditorialNarrativeSummaryView` | Types: `struct EditorialNarrativeSummaryView` |
| `client/newsly/newsly/Views/Components/ErrorView.swift` | `struct ErrorView` | Types: `struct ErrorView` |
| `client/newsly/newsly/Views/Components/FilterBar.swift` | `struct FilterBar` | Types: `struct FilterBar` |
| `client/newsly/newsly/Views/Components/FilterSheet.swift` | `struct FilterSheet` | Types: `struct FilterSheet` |
| `client/newsly/newsly/Views/Components/FullImageView.swift` | `struct FullImageView` | Types: `struct FullImageView` |
| `client/newsly/newsly/Views/Components/InterleavedSummaryV2View.swift` | `struct InterleavedSummaryV2View` | Types: `struct InterleavedSummaryV2View` |
| `client/newsly/newsly/Views/Components/InterleavedSummaryView.swift` | `struct InterleavedSummaryView` | Types: `struct InterleavedSummaryView` |
| `client/newsly/newsly/Views/Components/LiveVoiceActiveView.swift` | `struct LiveVoiceActiveView`, `body` | Types: `struct LiveVoiceActiveView`. Functions: `body` |
| `client/newsly/newsly/Views/Components/LiveVoiceAmbientBackground.swift` | `struct LiveVoiceAmbientBackground` | Types: `struct LiveVoiceAmbientBackground` |
| `client/newsly/newsly/Views/Components/LiveVoiceIdleView.swift` | `struct LiveVoiceIdleView` | Types: `struct LiveVoiceIdleView` |
| `client/newsly/newsly/Views/Components/LoadingView.swift` | `struct LoadingView` | Types: `struct LoadingView` |
| `client/newsly/newsly/Views/Components/LongFormCard.swift` | `struct LongFormCard` | Types: `struct LongFormCard` |
| `client/newsly/newsly/Views/Components/LongFormCardStackView.swift` | `struct LongFormCardStackView` | Calculate which index a dot at position i should represent |
| `client/newsly/newsly/Views/Components/NewsItemDetailView.swift` | `struct NewsItemDetailView` | Types: `struct NewsItemDetailView` |
| `client/newsly/newsly/Views/Components/NewsGroupCard.swift` | `struct NewsGroupCard` | Format publication date for compact display |
| `client/newsly/newsly/Views/Components/PagedCardView.swift` | `struct PagedCardView`, `struct GroupHeightPreferenceKey`, `reduce` | Types: `struct PagedCardView`, `struct GroupHeightPreferenceKey`. Functions: `reduce` |
| `client/newsly/newsly/Views/Components/PlaceholderCard.swift` | `struct PlaceholderCard` | Types: `struct PlaceholderCard` |
| `client/newsly/newsly/Views/Components/PlatformIcon.swift` | `struct PlatformIcon` | Types: `struct PlatformIcon` |
| `client/newsly/newsly/Views/Components/SafariView.swift` | `struct SafariView`, `makeUIViewController`, `updateUIViewController` | Types: `struct SafariView`. Functions: `makeUIViewController`, `updateUIViewController` |
| `client/newsly/newsly/Views/Components/SelectableMarkdownView.swift` | `struct SelectableMarkdownView`, `class Coordinator`, `struct MarkdownNSRenderer`, `makeCoordinator`, `makeUIView`, `render`, `sizeThatFits`, `updateUIView`, `withTraits` | A markdown-rendered text view that supports word-level text selection with "Dig Deeper" in the edit menu, using `DigDeeperTextView`. |
| `client/newsly/newsly/Views/Components/StructuredSummaryView.swift` | `struct StructuredSummaryView`, `struct ModernKeyPointRow`, `struct FlowLayout`, `struct FlowResult`, `struct Row`, `addToRow`, `finalizeRow`, `placeSubviews`, `sizeThatFits`, `widthInRow` | Types: `struct StructuredSummaryView`, `struct ModernKeyPointRow`, `struct FlowLayout`, `struct FlowResult`, `struct Row`. Functions: `addToRow`, `finalizeRow`, `placeSubviews`, `sizeThatFits`, `widthInRow` |
| `client/newsly/newsly/Views/Components/SubmissionStatusRow.swift` | `struct SubmissionStatusRow` | Types: `struct SubmissionStatusRow` |
| `client/newsly/newsly/Views/Components/SuggestionDetailSheet.swift` | `struct SuggestionDetailSheet` | Types: `struct SuggestionDetailSheet` |
| `client/newsly/newsly/Views/Components/SwipeableCard.swift` | `struct SwipeableCard` | Types: `struct SwipeableCard` |
| `client/newsly/newsly/Views/Components/ToastView.swift` | `struct ToastView`, `struct ToastModifier`, `body`, `withToast` | Types: `struct ToastView`, `struct ToastModifier`. Functions: `body`, `withToast` |
| `client/newsly/newsly/Views/Components/TweetSuggestionsSheet.swift` | `struct TweetSuggestionsSheet`, `struct TweetSuggestionCard` | Types: `struct TweetSuggestionsSheet`, `struct TweetSuggestionCard` |
