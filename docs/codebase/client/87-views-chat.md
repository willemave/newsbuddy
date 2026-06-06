# client/newsly/newsly/Views/Chat/

Source folder: `client/newsly/newsly/Views/Chat`

## Purpose
Chat-specific SwiftUI subviews used by `ChatSessionView` and chat previews.

## Runtime behavior
- `ChatSessionView` remains the screen shell; this folder owns message list, composer, bubbles, activity/error states, council branches, assistant feed options, and previews.
- Chat rendering consumes `ChatTimelineItem` and reconciled timeline state from `ChatSessionViewModel`/`ChatTimelineReconciler`.
- Preview fixture files provide local design/development data.

## Important files
| File | Purpose |
|---|---|
| `ChatMessageList.swift` | Scrollable chat timeline. |
| `ChatComposerDock.swift` | Input/composer dock. |
| `MessageRow.swift`, `MessageBubble.swift`, `UserMessageBubble.swift`, `AssistantMessageBubble.swift` | Message row and bubble presentation. |
| `ChatActivityViews.swift`, `ChatEmptyState.swift`, `ChatErrorBanner.swift` | Loading/empty/error states. |
| `AssistantFeedOptionsSection.swift`, `ArticlePreviewCard.swift` | Assistant feed options and article preview. |
| `CouncilBranchTabs.swift`, `CouncilCandidatesBubble.swift` | Council/candidate chat presentation. |
| `ChatPreviewFixtures.swift`, `ChatSessionViewPreviews.swift` | Preview-only data and previews. |

## Integration points
- Chat API calls live in `ChatService`; state lives in chat view models.
