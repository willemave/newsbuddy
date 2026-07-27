# client/newsly/newsly/

Source folder: `client/newsly/newsly`

## Purpose
Main SwiftUI app target root: app entrypoint, root content shell, target metadata, dependencies, assets, models, services, view models, and screens.

## Runtime behavior
- `newslyApp.swift` initializes app-level services, shared keychain setup, app settings, app chrome, and the typed Observation auth environment.
- `ContentView.swift` is the authenticated app shell; it owns shared state, the Briefing/Knowledge/Learning root paths, the More sheet, and tab selection.
- `Views/RootTabs.swift` renders the Briefing, Knowledge, Learning, and compact-tab surfaces.
- `Views/ContentRoutes.swift` registers shared content/chat/history/library navigation destinations for tab stacks.
- `E2ERouteInjector.swift` is the only app-shell reader for launch-time E2E content/chat routes; injected content opens on the Briefing navigation stack.
- `App/ChatDependencies.swift` builds chat-related service dependencies.
- The root flow handles tab state, app chrome, shared container state, and authenticated service wiring. Briefing is the only reading composition root; Knowledge and Learning remain peer tabs, while More is presented as a sheet. App-scoped polling, including active chat sessions plus the canonical unread/processing badge stats retries, is suspended while the scene is inactive or backgrounded.

## Important files and folders
| Path | Purpose |
|---|---|
| `newslyApp.swift` | SwiftUI `App` entrypoint and root dependency setup. |
| `ContentView.swift` | Authenticated app shell, shared state wiring, and tab selection. |
| `E2ERouteInjector.swift` | E2E launch route injection helper. |
| `App/ChatDependencies.swift` | Chat dependency factory. |
| `Views/ContentRoutes.swift` | Shared navigation destinations for tab stacks. |
| `Views/RootTabs.swift` | Root tab view structs and compact briefing tab bar composition. |
| `Info.plist` | Target metadata and URL/background capabilities. |
| `newsly.entitlements` | App entitlements, including app-group/keychain capabilities. |
| `Models/`, `Services/`, `Repositories/`, `Shared/`, `ViewModels/`, `Views/` | Main app source layers. |
| `Assets.xcassets/`, `Fonts/` | Visual assets and bundled fonts. |

## Notes
- Generated contracts live under `Models/Generated`.
