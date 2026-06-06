# Client Assets and Fonts

Source folders: `client/newsly/newsly/Assets.xcassets`, `client/newsly/newsly/Fonts`

## Purpose
Checked-in visual assets and bundled fonts for the iOS app.

## Runtime behavior
- Asset catalogs provide the app icon, accent color, mascot, and provider icons used by settings/integration surfaces.
- Fonts include Inter and Newsreader regular/italic files for app typography and reader-style presentation.
- Target membership and Info.plist font registration must stay aligned with Xcode project settings.

## Important paths
| Path | Purpose |
|---|---|
| `Assets.xcassets/AppIcon.appiconset` | App icon assets. |
| `Assets.xcassets/AccentColor.colorset` | App accent color asset. |
| `Assets.xcassets/Mascot.imageset` | Mascot artwork. |
| `Assets.xcassets/openai-icon.imageset`, `gemini-icon.imageset`, `claude-icon.imageset` | Provider integration icons. |
| `Fonts/Inter.ttf`, `Fonts/Inter-Italic.ttf` | Inter font files. |
| `Fonts/Newsreader.ttf`, `Fonts/Newsreader-Italic.ttf` | Newsreader font files. |
