# Client Assets and Fonts

Source folders: `client/newsly/newsly/Assets.xcassets`, `client/newsly/newsly/Fonts`

## Purpose
Checked-in visual assets and bundled fonts for the iOS app.

## Runtime behavior
- Asset catalogs provide the app icon, accent color, mascot, and provider icons used by settings/integration surfaces.
- Fonts include Inter, Libre Franklin, and Source Serif 4 files for app typography and reader-style presentation.
- Target membership and Info.plist font registration must stay aligned with Xcode project settings.

## Important paths
| Path | Purpose |
|---|---|
| `Assets.xcassets/AppIcon.appiconset` | App icon assets. |
| `Assets.xcassets/AccentColor.colorset` | App accent color asset. |
| `Assets.xcassets/Mascot.imageset` | Mascot artwork. |
| `Assets.xcassets/openai-icon.imageset`, `gemini-icon.imageset`, `claude-icon.imageset` | Provider integration icons. |
| `Fonts/Inter.ttf`, `Fonts/Inter-Italic.ttf` | Inter font files. |
| `Fonts/LibreFranklin-Regular.ttf`, `Fonts/LibreFranklin-Italic.ttf` | Libre Franklin body/UI font files. |
| `Fonts/SourceSerif4-Light.ttf`, `Fonts/SourceSerif4-LightItalic.ttf` | Source Serif 4 title font files. |
