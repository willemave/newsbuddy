# client/newsly/newsly/

Source folder: `client/newsly/newsly`

## Purpose
SwiftUI app target root containing the `App` entrypoint, primary tab container, Info.plist metadata, and target entitlements.

## Runtime behavior
- Bootstraps authentication-driven root presentation and injects shared state into the authenticated SwiftUI shell.
- Defines app-wide configuration such as bundle metadata, entitlements, and the root `ContentView` tab/navigation container.
- Delegates most feature logic into Models, Services, ViewModels, and Views subfolders documented separately.

## Inventory scope
- Direct file inventory for `client/newsly/newsly`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/ContentView.swift` | `struct ContentView`, `withContentRoutes` | Types: `struct ContentView`. Functions: `withContentRoutes` |
| `client/newsly/newsly/Info.plist` | n/a | Supporting module or configuration file. |
| `client/newsly/newsly/newsly.entitlements` | n/a | Supporting module or configuration file. |
| `client/newsly/newsly/newslyApp.swift` | `struct newslyApp` | Types: `struct newslyApp` |
