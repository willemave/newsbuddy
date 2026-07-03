# client/newsly/newsly/Views/Briefing/

Source folder: `client/newsly/newsly/Views/Briefing`

## Purpose
Native SwiftUI renderer for the Briefing reading experience: an unread-edition tab with lens
paging, server-normalized passage runs, image/pullquote blocks, source sheets, live dig-deeper,
and audio narration controls.

## Runtime behavior
- `BriefingView` owns the tab surface, lens strip, page container, bottom narration bar, dig panel,
  and source/Safari sheets.
- `BriefingPassageView` bridges `DigDeeperTextView` so server-normalized runs can expose tappable
  source links and selectable text without a WebView.
- `BriefingAttributedTextBuilder` converts generated `APIBriefingParagraph`/`APIBriefingRun`
  payloads into attributed text with source-link URLs and insight markers.
- Source sheets open content sources in `ContentDetailView` with `.briefing` navigation context and
  news sources in a compact native summary sheet.

## Important files
| File | Purpose |
|---|---|
| `BriefingView.swift` | Top-level Briefing UI and private subviews for lens pages, blocks, source sheets, dig panel, and narration. |
| `BriefingPassageView.swift` | UIKit text bridge for links, selection, and insight taps. |
| `BriefingAttributedTextBuilder.swift` | Attributed string builder for source links and insight run metadata. |

## Integration points
- `ContentView` swaps Classic Long/Fast tabs for the Briefing tab when `AppSettings.readingExperience == .briefing`.
- `BriefingViewModel` provides index/lens/read/narration state.
- `BriefingService` owns HTTP calls to `/api/briefing*`.
