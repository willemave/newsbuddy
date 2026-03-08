# Client Reference

Folder-by-folder reference for the SwiftUI app, share extension, project metadata, and supporting client scripts/tests.

## What this section covers
- Use this section to trace backend contracts into Swift models, services, view models, and screens.
- Build artifacts are intentionally excluded; the reference focuses on source, project, and extension folders.

## Documents
| Doc | Source folder | Focus |
|---|---|---|
| `10-workspace.md` | `client/newsly` | Xcode workspace root and app-level configuration: xcconfig files, secrets templates, sync helpers, and the top-level package/project layout for the iOS client. |
| `20-app-target-root.md` | `client/newsly/newsly` | SwiftUI app target root containing the `App` entrypoint, primary tab container, Info.plist metadata, and target entitlements. |
| `30-models.md` | `client/newsly/newsly/Models` | Typed client-side models for API payloads, navigation routes, summaries, content metadata, discovery results, chat, onboarding, and live voice. |
| `31-models-generated.md` | `client/newsly/newsly/Models/Generated` | Generated API contract models synchronized from the backend schema for places where the client wants compile-time alignment with exported OpenAPI contracts. |
| `40-repositories.md` | `client/newsly/newsly/Repositories` | Repository layer that wraps `APIClient` calls for content, read-state, and daily digest endpoints into higher-level async methods used by view models. |
| `50-services.md` | `client/newsly/newsly/Services` | App services for authentication, API transport, websocket voice, image caching, notifications, settings, chat helpers, discovery, and background/shared state. |
| `60-shared.md` | `client/newsly/newsly/Shared` | Shared observable state and container helpers reused across tabs, detail flows, onboarding, and the share extension. |
| `70-view-models.md` | `client/newsly/newsly/ViewModels` | ObservableObject view models coordinating repositories, services, and navigation state for list/detail screens, onboarding, discovery, live voice, and chat. |
| `80-views.md` | `client/newsly/newsly/Views` | Top-level SwiftUI screens for tabs, feature entrypoints, and major routed surfaces. |
| `81-views-components.md` | `client/newsly/newsly/Views/Components` | Reusable SwiftUI building blocks for cards, summaries, markdown rendering, filters, live voice states, discovery cards, toasts, and media presentation. |
| `82-views-onboarding.md` | `client/newsly/newsly/Views/Onboarding` | New-user onboarding flow UI including reveal animation, mic interaction, and tutorial/explanatory surfaces. |
| `83-views-settings.md` | `client/newsly/newsly/Views/Settings` | SwiftUI settings screens for account, appearance, integrations, and app-level preferences. |
| `84-views-shared.md` | `client/newsly/newsly/Views/Shared` | Cross-feature presentation primitives and design tokens such as cards, chips, headers, dividers, search bars, and branded backgrounds. |
| `85-views-sources.md` | `client/newsly/newsly/Views/Sources` | Source-management screens for feed and podcast subscriptions plus source-detail presentation. |
| `86-views-library.md` | `client/newsly/newsly/Views/Library` | Library-oriented SwiftUI surfaces for saved/favorited content. |
| `90-share-extension.md` | `client/newsly/ShareExtension` | Share extension target that receives shared URLs from iOS, reads shared auth state, and forwards submissions into the backend pipeline. |
| `94-scripts.md` | `client/newsly/scripts` | Client-specific helper scripts for regenerating derived assets such as API contracts. |
| `95-tests.md` | `client/newsly/newslyTests` | Focused iOS unit tests covering share routing, onboarding animation progress, and daily-digest dig-deeper behavior. |
| `96-xcode-project.md` | `client/newsly/newsly.xcodeproj` | Xcode project metadata including schemes, workspace settings, package resolution, and target membership for the app and share extension. |

## Concat command
```bash
find docs/codebase/client -type f -name '*.md' | sort | xargs cat
```
