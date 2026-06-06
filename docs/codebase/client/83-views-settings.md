# client/newsly/newsly/Views/Settings/

Source folder: `client/newsly/newsly/Views/Settings`

## Purpose
Settings-related SwiftUI screens and helpers for account/app preferences, reader palette, X integration, CLI linking, and settings layout.

## Runtime behavior
- `SettingsView` is the main settings surface.
- `ReaderPaletteSettingsView` controls reader palette/theme preferences.
- `TwitterSettingsView` manages X/Twitter connection state.
- `CLILinkScannerSheet` handles CLI QR link approval flow.
- `SettingsCardModifier` and settings shared rows provide consistent layout/styling.

## Important files
| File | Purpose |
|---|---|
| `SettingsView.swift` | Main settings screen. |
| `ReaderPaletteSettingsView.swift` | Reader palette settings. |
| `TwitterSettingsView.swift` | X/Twitter integration settings. |
| `CLILinkScannerSheet.swift` | CLI QR link scanner/approval sheet. |
| `SettingsCardModifier.swift` | Settings card styling helper. |

## Integration points
- Uses services such as `XIntegrationService`, `CLILinkService`, and shared stores like `ReaderPalette`.
