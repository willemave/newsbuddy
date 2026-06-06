# client/newsly/newsly/Views/Components/

Source folder: `client/newsly/newsly/Views/Components`

## Purpose
Reusable SwiftUI building blocks for content cards, summaries, markdown/text rendering, discovery cards, media, Learning Decks, narration, Quick Mic, sheets, toasts, and shared presentation states.

## Runtime behavior
- Components should be stateless or locally stateful presentation helpers; feature state belongs in view models/services.
- Summary/card components are reused across long-form, short-form, Knowledge, search, and detail surfaces.
- Sheet components expose focused feature workflows such as Learning Deck creation/listing, tweet suggestions, download-more, custom narration controls, and feed suggestions.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Cards and lists | `ArticleCardView.swift`, `ContentCard.swift`, `LongFormCard.swift`, `LongFormCardStackView.swift`, `NewsGroupCard.swift`, `PagedCardView.swift`, `SwipeableCard.swift`, `PlaceholderCard.swift`, `ChatSessionCard.swift` | Reusable content/news/chat cards. |
| Summary rendering | `BulletedSummaryView.swift`, `EditorialMastheadHeader.swift`, `EditorialNarrativeSummaryView.swift`, `FastReadBriefingComponents.swift`, `InterleavedSummaryView.swift`, `InterleavedSummaryV2View.swift`, `StructuredSummaryView.swift`, `LongformArtifactView.swift` | Summary and artifact presentation. |
| Markdown/text/media | `ChatMarkdownTheme.swift`, `SelectableMarkdownView.swift`, `SelectableText.swift`, `CachedAsyncImage.swift`, `FullImageView.swift`, `SafariView.swift`, `PlatformIcon.swift` | Markdown, selectable text, images, Safari presentation, and platform icons. |
| Discovery/source | `DetectedFeedCard.swift`, `DiscoveryRunSection.swift`, `DiscoveryStateViews.swift`, `DiscoverySuggestionCard.swift`, `SourceMetadataSection.swift`, `SuggestionDetailSheet.swift` | Discovery suggestions and source metadata. |
| Actions and sheets | `DownloadMoreMenu.swift`, `TweetSuggestionsSheet.swift`, `ChatShareSheet.swift`, `LearningDeckCreateSheet.swift`, `LearningDeckContentCreateSheet.swift`, `LearningDeckListSheet.swift`, `LearningDeckRow.swift` | User action surfaces. |
| Narration and Quick Mic | `NarrationPlaybackControlRow.swift`, `NarrationPressButton.swift`, `QuickMicContext.swift`, `QuickMicOverlay.swift`, `DigDeeperTextView.swift` | Audio/narration and mic interaction components. |
| Shared states | `LoadingView.swift`, `ChatLoadingView.swift`, `ErrorView.swift`, `ToastView.swift`, `ChatStatusBanner.swift`, `FilterBar.swift`, `FilterSheet.swift`, `ContentTimestampText.swift`, `ContentTypeBadge.swift`, `SubmissionStatusRow.swift` | Loading/error/filter/status primitives. |
| News detail | `NewsItemDetailView.swift` | Fast Read detail component. |

## Integration points
- Components are imported by top-level views and should avoid owning API calls directly.
- Chat-specific components live in `Views/Chat` and are documented separately.
