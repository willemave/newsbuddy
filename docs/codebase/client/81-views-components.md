# client/newsly/newsly/Views/Components/

Source folder: `client/newsly/newsly/Views/Components`

## Purpose
Reusable SwiftUI building blocks for content cards, summaries, markdown/text rendering, media, Learning Decks, narration, sheets, toasts, and shared presentation states.

## Runtime behavior
- Components should be stateless or locally stateful presentation helpers; feature state belongs in view models/services.
- Summary/card components are reused across Knowledge, search, Briefing, and detail surfaces.
- Sheet components expose focused feature workflows such as Learning Deck creation/listing, tweet suggestions, custom narration controls, and feed suggestions.
- `TapToTalkMicButton` owns shared voice haptics through SwiftUI `.sensoryFeedback`: light impact when recording starts, success when transcription begins.
- Shared control, card, row, and sheet state animations use `AppMotion` tokens instead of per-surface timing constants.
- `CachedAsyncImage` image fades use `AppMotion.subtle` through the Reduce Motion helper instead of local duration constants.
- Detail and tweet-sheet decorative symbol effects are disabled when Reduce Motion is enabled while leaving their state icons visible.

## Important groups
| Group | Files | Purpose |
|---|---|---|
| Cards and lists | `ContentCard.swift`, `ChatSessionCard.swift` | Reusable content and chat cards. |
| Summary rendering | `DetailSummarySections.swift`, `BulletedSummaryView.swift`, `EditorialMastheadHeader.swift`, `EditorialNarrativeSummaryView.swift`, `FastReadBriefingComponents.swift`, `InterleavedSummaryView.swift`, `InterleavedSummaryV2View.swift`, `StructuredSummaryView.swift`, `LongformArtifactView.swift` | Detail summary selection, summary presentation, and artifact presentation. |
| Markdown/text/media | `ChatMarkdownTheme.swift`, `SelectableMarkdownView.swift`, `CachedAsyncImage.swift`, `FullImageView.swift`, `SafariView.swift`, `PlatformIcon.swift` | Markdown, selectable text, images, Safari presentation, and platform icons. |
| Discovery/source | `DetectedFeedCard.swift`, `SourceMetadataSection.swift` | Feed detection and source metadata. |
| Actions and sheets | `TweetSuggestionsSheet.swift`, `ChatShareSheet.swift`, `DetailActionBar.swift`, `DetailChatSheet.swift`, `DetailHeroHeader.swift`, `DetailMiniSheets.swift`, `MiniSheetComponents.swift`, `LearningDeckCreateSheet.swift`, `LearningDeckContentCreateSheet.swift` | User action surfaces, detail hero chrome, and compact sheet controls. |
| Narration and voice | `NarrationPlaybackControlRow.swift`, `NarrationPressButton.swift`, `TapToTalkMicButton.swift`, `DigDeeperTextView.swift` | Audio/narration and mic interaction components. |
| Shared states | `LoadingView.swift`, `ChatLoadingView.swift`, `ErrorView.swift`, `ToastView.swift`, `ChatStatusBanner.swift`, `FilterSheet.swift`, `ContentTimestampText.swift`, `SubmissionStatusRow.swift`, `ExpandableSection.swift` | Loading/error/filter/status and reusable section primitives. |
| News detail | `NewsItemDetailView.swift`, `DetailContentSections.swift`, `DiscussionSheet.swift`, `DiscussionCommentIndexer.swift`, `RelevantLinksSection.swift`, `PodcastAudioPromptCard.swift`, `ContentDetailPresentationModels.swift` | Fast Read detail, detail body sections, discussion presentation/indexing, relevant links, podcast audio prompt, and small detail presentation models. |

## Integration points
- Components are imported by top-level views and should avoid owning API calls directly.
- Chat-specific components live in `Views/Chat` and are documented separately.
