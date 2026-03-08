# client/newsly/newsly/Views/

Source folder: `client/newsly/newsly/Views`

## Purpose
Top-level SwiftUI screens for tabs, feature entrypoints, and major routed surfaces.

## Runtime behavior
- Defines the primary user-facing screens such as long-form, short-form, knowledge, submissions, search, debug, and authentication flows.
- Delegates reusable view pieces into `Views/Components`, `Views/Shared`, and feature-specific subfolders.

## Inventory scope
- Direct file inventory for `client/newsly/newsly/Views`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/Views/AuthenticatedRootView.swift` | `struct AuthenticatedRootView` | Types: `struct AuthenticatedRootView` |
| `client/newsly/newsly/Views/AuthenticationView.swift` | `struct AuthenticationView` | Login screen with Apple Sign In |
| `client/newsly/newsly/Views/ChatSessionView.swift` | `struct ShareContent`, `struct ShareSheet`, `struct SelectableText`, `class Coordinator`, `class DigDeeperTextView`, `struct SelectableAttributedText`, `class Coordinator`, `struct ChatSessionView`, `struct MessageBubble`, `struct ProcessSummaryRow`, +8 more | Custom UITextView that adds "Dig Deeper" to the edit menu |
| `client/newsly/newsly/Views/ContentDetailView.swift` | `struct ContentDetailView`, `computeDescendantCount` | Build one reusable index for comment rendering. |
| `client/newsly/newsly/Views/ContentListView.swift` | `struct ContentListView`, `struct ContentListView_Previews` | Types: `struct ContentListView`, `struct ContentListView_Previews` |
| `client/newsly/newsly/Views/DailyDigestShortFormView.swift` | `struct DailyDigestShortFormView` | Types: `struct DailyDigestShortFormView` |
| `client/newsly/newsly/Views/DebugMenuView.swift` | `struct DebugMenuView`, `struct TokenInputView` | Types: `struct DebugMenuView`, `struct TokenInputView` |
| `client/newsly/newsly/Views/DiscoveryPersonalizeSheet.swift` | `struct DiscoveryPersonalizeSheet` | Types: `struct DiscoveryPersonalizeSheet` |
| `client/newsly/newsly/Views/KnowledgeDiscoveryView.swift` | `struct KnowledgeDiscoveryView` | Types: `struct KnowledgeDiscoveryView` |
| `client/newsly/newsly/Views/KnowledgeLiveView.swift` | `struct KnowledgeLiveView` | Types: `struct KnowledgeLiveView` |
| `client/newsly/newsly/Views/KnowledgeView.swift` | `struct KnowledgeView`, `struct ChatSessionCard`, `struct NewChatSheet` | Tracks the last time this tab was opened for badge calculation |
| `client/newsly/newsly/Views/LandingView.swift` | `struct LandingView` | Types: `struct LandingView` |
| `client/newsly/newsly/Views/LongFormView.swift` | `struct LongFormView` | Types: `struct LongFormView` |
| `client/newsly/newsly/Views/MoreView.swift` | `struct MoreView` | Types: `struct MoreView` |
| `client/newsly/newsly/Views/ProcessingStatsView.swift` | `struct ProcessingStatsView` | Types: `struct ProcessingStatsView` |
| `client/newsly/newsly/Views/RecentlyReadView.swift` | `struct RecentlyReadView` | Types: `struct RecentlyReadView` |
| `client/newsly/newsly/Views/SearchView.swift` | `struct SearchView` | Types: `struct SearchView` |
| `client/newsly/newsly/Views/ShortFormView.swift` | `struct ShortFormView` | Track which items have already been marked as read to avoid duplicates |
| `client/newsly/newsly/Views/SubmissionDetailView.swift` | `struct SubmissionDetailView` | Types: `struct SubmissionDetailView` |
| `client/newsly/newsly/Views/SubmissionsView.swift` | `struct SubmissionsView` | Types: `struct SubmissionsView` |
