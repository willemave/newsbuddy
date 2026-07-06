# client/newsly/newsly/

Source folder: `client/newsly/newsly`

## Purpose
Main SwiftUI app target root: app entrypoint, root content shell, target metadata, dependencies, assets, models, services, view models, and screens.

## Runtime behavior
- `newslyApp.swift` initializes app-level services, shared keychain setup, app settings, app chrome, and the typed Observation auth environment.
- `ContentView.swift` is the authenticated app shell; it owns shared state, root paths, route restoration, and tab selection.
- `Views/RootTabs.swift` renders the long-form, fast-news, briefing, knowledge, more, and compact briefing-tab surfaces.
- `Views/ContentRoutes.swift` registers shared content/chat/history/library navigation destinations for tab stacks.
- `NavigationRestorationModel.swift` restores the last-read detail route, while `E2ERouteInjector.swift` is the only app-shell reader for launch-time E2E content/chat routes.
- `App/ChatDependencies.swift` builds chat-related service dependencies.
- The root flow handles CLI link URLs, tab state, route restoration, app chrome, shared container state, and authenticated service wiring. Re-selecting the active long-form or fast-news root tab sends a scroll-to-top request and light haptic feedback without refreshing long-form content. App-scoped polling, including active chat sessions plus unread/processing badge stats retries, is suspended while the scene is inactive or backgrounded.

## Important files and folders
| Path | Purpose |
|---|---|
| `newslyApp.swift` | SwiftUI `App` entrypoint and root dependency setup. |
| `ContentView.swift` | Authenticated app shell, shared state wiring, and tab selection. |
| `E2ERouteInjector.swift` | E2E launch route injection helper. |
| `NavigationRestorationModel.swift` | Last-read route restoration helper. |
| `RootTab+Availability.swift` | Root-tab availability mapping for briefing versus classic reading modes. |
| `RootTabSelectionModel.swift` | Root-tab selection, availability, and active-tab re-selection behavior. |
| `App/ChatDependencies.swift` | Chat dependency factory. |
| `Views/ContentRoutes.swift` | Shared navigation destinations for tab stacks. |
| `Views/RootTabs.swift` | Root tab view structs and compact briefing tab bar composition. |
| `Info.plist` | Target metadata and URL/background capabilities. |
| `newsly.entitlements` | App entitlements, including app-group/keychain capabilities. |
| `Models/`, `Services/`, `Repositories/`, `Shared/`, `ViewModels/`, `Views/` | Main app source layers. |
| `Assets.xcassets/`, `Fonts/` | Visual assets and bundled fonts. |

## Notes
- Generated contracts live under `Models/Generated`.
