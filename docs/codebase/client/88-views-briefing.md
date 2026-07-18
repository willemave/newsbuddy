# client/newsly/newsly/Views/Briefing/

Source folder: `client/newsly/newsly/Views/Briefing`

## Purpose
Native SwiftUI renderer for the Briefing reading experience: an unread-edition tab with lens
paging, server-normalized passage runs, image/pullquote blocks, source sheets, live dig-deeper,
and audio narration controls.

## Runtime behavior
- `BriefingView` owns the tab surface, lens strip, page container, bottom narration bar, dig panel,
  and source/Safari sheets.
- `BriefingPassageView` bridges `DigDeeperTextView` so server-normalized runs expose tappable
  source links plus native touch-and-hold selection with a custom Dig Deeper edit-menu action.
- `BriefingAttributedTextBuilder` converts generated `APIBriefingParagraph`/`APIBriefingRun`
  payloads into attributed text with source-link URLs. Insight runs render as ordinary body text.
- Source sheets open content sources in `ContentDetailView` with `.briefing` navigation context and
  news sources in a compact native summary sheet.

## Important files
| File | Purpose |
|---|---|
| `BriefingView.swift` | Top-level Briefing UI and private subviews for lens pages, blocks, source sheets, dig panel, and narration. |
| `BriefingPassageView.swift` | UIKit text bridge for links and selection. |
| `BriefingAttributedTextBuilder.swift` | Attributed string builder for body text, source links, and discussion chips. |

## Integration points
- `AuthenticatedRootView` resolves production sessions to Briefing even when a legacy server profile still says Classic. `ContentView` retains the Classic Long/Fast shell only for explicit fallback and E2E coverage.
- `BriefingViewModel` provides index/lens/read/narration state.
- `BriefingService` owns HTTP calls to `/api/briefing*`.
