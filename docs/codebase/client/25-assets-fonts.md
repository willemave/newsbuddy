# Client Assets and Fonts

Source folders: `client/newsly/newsly/Assets.xcassets`, `client/newsly/newsly/Fonts`, `client/newsly/ShareExtension/Fonts`

## Purpose
Checked-in visual assets and bundled fonts for the iOS app and Share Extension.

## Runtime behavior
- Asset catalogs provide the app icon, accent color, mascot, and provider icons used by settings/integration surfaces.
- Fonts include Lato and Lora files for app and share extension typography.
- Target membership and Info.plist font registration must stay aligned with Xcode project settings.

## Important paths
| Path | Purpose |
|---|---|
| `Assets.xcassets/AppIcon.appiconset` | App icon assets. |
| `Assets.xcassets/AccentColor.colorset` | App accent color asset. |
| `Assets.xcassets/Mascot.imageset` | Mascot artwork. |
| `Assets.xcassets/openai-icon.imageset`, `gemini-icon.imageset`, `claude-icon.imageset` | Provider integration icons. |
| `Fonts/Lato.ttf`, `Fonts/Lato-Italic.ttf` | Lato UI/body font files. |
| `Fonts/Lora.ttf`, `Fonts/Lora-Italic.ttf` | Lora title font files. |
