# Spec: Restore "Dig Deeper" in Selected Text Menus (Chat + Content Detail)

## Goal
Provide a consistent "Dig Deeper" action for selected text across chat messages and content detail views (long/short form). The action should appear in the iOS selection menu and trigger the existing dig-deeper flow.

## Requirements
- Selection menu shows "Dig Deeper" when text selection is non-empty.
- Works for Markdown-rendered content and plain text.
- Uses a shared implementation across ChatSessionView and ContentDetailView.
- Keeps system actions (Copy, Look Up, Share) intact.

## Approach
1) Introduce a reusable UIKit-backed selectable text component.
2) Render markdown as attributed text inside a UITextView to support selection + custom menu.
3) Wire the same "Dig Deeper" handler in chat and content detail.

## Implementation Outline
### New component
- `client/newsly/newsly/Views/Components/SelectableTextView.swift`
- UIKit-backed `UITextView` wrapper with:
  - `isSelectable = true`, `isEditable = false`, `isScrollEnabled = false`
  - custom menu item "Dig Deeper" in edit menu
  - `onDigDeeper(selectedText: String)` callback

### Markdown rendering
- Use the existing Markdown library in the repo (MarkdownUI) to produce attributed output.
- If MarkdownUI-to-attributed conversion is not available, fall back to `NSAttributedString(markdown:)`.
- Ensure consistent font sizing and link styling.

### Integration points
- `ChatSessionView` message bubbles:
  - Replace MarkdownUI-only rendering with `SelectableTextView`.
  - Pass `onDigDeeper` to trigger chat dig deeper flow.
- `ContentDetailView` long/short form markdown and transcript:
  - Replace MarkdownUI `Markdown(...)` with `SelectableTextView`.
  - Pass `onDigDeeper` to open the same dig deeper flow.

## Acceptance Criteria
- Selecting text in chat bubbles shows "Dig Deeper" in the menu.
- Selecting text in long/short form content detail shows "Dig Deeper".
- Action uses the selected text and triggers existing dig-deeper behavior.
- No loss of markdown formatting quality.

## Reminder
Come back and implement after scroll behavior is stable.
