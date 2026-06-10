# client/newsly/newsly/

Source folder: `client/newsly/newsly`

## Purpose
Main SwiftUI app target root: app entrypoint, root content shell, target metadata, dependencies, assets, models, services, view models, and screens.

## Runtime behavior
- `newslyApp.swift` initializes app-level services, shared keychain setup, app settings, app chrome, and environment objects.
- `ContentView.swift` selects authentication versus authenticated root flow and coordinates tab/root state.
- `App/ChatDependencies.swift` builds chat-related service dependencies.
- The root flow handles CLI link URLs, tab state, route restoration, app chrome, shared container state, and authenticated service wiring.

## Important files and folders
| Path | Purpose |
|---|---|
| `newslyApp.swift` | SwiftUI `App` entrypoint and root dependency setup. |
| `ContentView.swift` | Top-level authenticated/unauthenticated root switch. |
| `App/ChatDependencies.swift` | Chat dependency factory. |
| `Info.plist` | Target metadata and URL/background capabilities. |
| `newsly.entitlements` | App entitlements, including app-group/keychain capabilities. |
| `Models/`, `Services/`, `Repositories/`, `Shared/`, `ViewModels/`, `Views/` | Main app source layers. |
| `Assets.xcassets/`, `Fonts/` | Visual assets and bundled fonts. |

## Notes
- `Info.plist.backup` is checked in but should not be treated as an active runtime source without verifying target membership.
- Generated contracts live under `Models/Generated`.
