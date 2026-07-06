# client/newsly/newsly/Views/Settings/

Source folder: `client/newsly/newsly/Views/Settings`

## Purpose
Settings-related SwiftUI screens and helpers for account/app preferences, reader palette, X integration, CLI linking, settings layout, and per-section settings rows.

## Runtime behavior
- `SettingsView` owns settings state, alerts, sheets, and async actions while `SettingsSectionStack` composes the visible settings sections.
- Dedicated section views render account, display, council, source, read-status, feedback, X/Twitter, and debug rows.
- `ReaderPaletteSettingsView` controls reader palette/theme preferences.
- `TwitterSettingsView` manages X/Twitter connection state.
- `CLILinkScannerSheet` handles CLI QR link approval flow.
- `SettingsCardModifier` and settings shared rows provide consistent layout/styling.

## Important files
| File | Purpose |
|---|---|
| `AuthState+Settings.swift` | Settings-local authenticated-user helper. |
| `CLILinkScannerSheet.swift` | CLI QR link scanner/approval sheet. |
| `CouncilPersona+Settings.swift` | Settings-local council persona normalization helper. |
| `MarkAllTarget.swift` | Mark-all-as-read dialog targets and labels. |
| `ReaderPaletteSettingsView.swift` | Reader palette settings. |
| `SettingsAccountSection.swift` | Account card, CLI link row, and sign-out row. |
| `SettingsBrandHeader.swift` | Mascot/title/version header. |
| `SettingsCardModifier.swift` | Settings card styling helper. |
| `SettingsCouncilSection.swift` | Council expert editing section. |
| `SettingsDebugSection.swift` | Debug menu row for simulator debug builds. |
| `SettingsDisplaySection.swift` | Reading experience and text-size settings. |
| `SettingsFeedbackSection.swift` | Feedback entry row. |
| `SettingsFeedbackSheet.swift` | Feedback composer sheet. |
| `SettingsReadStatusSection.swift` | Mark-all-as-read row. |
| `SettingsSectionStack.swift` | Vertical composition of the settings sections. |
| `SettingsSourcesSection.swift` | Feed and podcast source navigation rows. |
| `SettingsTwitterSection.swift` | X/Twitter navigation row and status subtitle. |
| `SettingsView.swift` | Main settings shell and async actions. |
| `TwitterSettingsView.swift` | X/Twitter integration settings. |

## Integration points
- Uses services such as `XIntegrationService`, `CLILinkService`, and shared stores like `ReaderPalette`.
