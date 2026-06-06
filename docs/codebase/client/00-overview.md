# Client Reference

Folder-by-folder reference for the SwiftUI app, share extension, generated contract artifacts, client scripts/tests, assets, and Xcode project metadata.

## What this section covers
- Use this section to trace backend contracts into Swift models, services, view models, and screens.
- Build artifacts and local Xcode user state are intentionally excluded; source, checked-in generated contracts, assets, scripts, and test surfaces are included.

## Documents
| Doc | Source folder | Focus |
|---|---|---|
| `10-workspace.md` | `client/newsly` | Workspace root, xcconfig files, secret sync template, OpenAPI generator config, client scripts, and top-level project layout. |
| `20-app-target-root.md` | `client/newsly/newsly` | SwiftUI app entrypoint, root tab/container, app dependencies, Info.plist, entitlements, route restoration, and shared app setup. |
| `25-assets-fonts.md` | `client/newsly/newsly/Assets.xcassets`, `client/newsly/newsly/Fonts` | App icons, accent/mascot/provider assets, and bundled Inter/Newsreader fonts. |
| `30-models.md` | `client/newsly/newsly/Models` | Typed client-side models for content, news, chat, search, Knowledge, Learning Decks, narration, onboarding, routes, source metadata, and API payloads. |
| `31-models-generated.md` | `client/newsly/newsly/Models/Generated`, `client/newsly/OpenAPI/Generated` | Generated Swift API contracts and Swift OpenAPI client/types. |
| `40-repositories.md` | `client/newsly/newsly/Repositories` | Repository layer for content and read-state operations. |
| `50-services.md` | `client/newsly/newsly/Services` | App services for auth, API transport, content/news APIs, chat, discovery, audio episodes, Learning Decks, CLI link, feedback, dictation/transcription, settings, notifications, images, and integrations. |
| `60-shared.md` | `client/newsly/newsly/Shared` | Shared app state, dependency factory, reader palette, app chrome, and app-group container helpers. |
| `70-view-models.md` | `client/newsly/newsly/ViewModels` | Observable view models for lists, details, chat, Knowledge, discovery, onboarding, audio/narration, Learning Decks, search, settings, and tab coordination. |
| `80-views.md` | `client/newsly/newsly/Views` | Top-level SwiftUI screens and routed feature surfaces. |
| `81-views-components.md` | `client/newsly/newsly/Views/Components` | Reusable content cards, summary renderers, sheets, image loading, narration, Learning Deck, Quick Mic, and presentation components. |
| `82-views-onboarding.md` | `client/newsly/newsly/Views/Onboarding` | New-user onboarding flow UI and reveal/mic surfaces. |
| `83-views-settings.md` | `client/newsly/newsly/Views/Settings` | Settings, reader palette, X integration, CLI link, and settings-row/card helpers. |
| `84-views-shared.md` | `client/newsly/newsly/Views/Shared` | Cross-feature design tokens and presentation primitives. |
| `85-views-sources.md` | `client/newsly/newsly/Views/Sources` | Feed and podcast source-management screens. |
| `86-views-library.md` | `client/newsly/newsly/Views/Library` | Library/Favorites screens. |
| `87-views-chat.md` | `client/newsly/newsly/Views/Chat` | Chat message list, composer dock, assistant/council bubbles, feed options, preview fixtures, and chat-specific subviews. |
| `90-share-extension.md` | `client/newsly/ShareExtension` | Share extension modes for content, Learning Decks, links, feeds, and chat. |
| `94-scripts.md` | `client/newsly/scripts` | Client-specific contract regeneration script. |
| `95-tests.md` | `client/newsly/newslyTests`, `client/newsly/newslyUITests` | iOS unit and UI test inventory. |
| `96-xcode-project.md` | `client/newsly/newsly.xcodeproj` | Xcode project metadata, app/share extension targets, schemes, and Swift package pins. |

## Concat command
```bash
find docs/codebase/client -type f -name '*.md' | sort | xargs cat
```
