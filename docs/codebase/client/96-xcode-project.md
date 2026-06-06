# client/newsly/newsly.xcodeproj/

Source folder: `client/newsly/newsly.xcodeproj`

## Purpose
Xcode project metadata for the iOS app, share extension, tests, schemes, workspace settings, and Swift package pins.

## Runtime behavior
- `project.pbxproj` defines the app target, share extension target, unit/UI test targets, build phases, source membership, entitlements, asset/font membership, and package dependencies.
- `xcshareddata/xcschemes/newsly.xcscheme` defines the shared scheme used for builds/tests.
- `project.xcworkspace/xcshareddata/swiftpm/Package.resolved` pins Swift Package Manager dependencies such as MarkdownUI.
- `xcuserdata` and user-interface state files are local Xcode noise and should not be treated as durable source.

## Important files
| File | Purpose |
|---|---|
| `project.pbxproj` | Target/build/package metadata. |
| `project.xcworkspace/contents.xcworkspacedata` | Workspace declaration. |
| `project.xcworkspace/xcshareddata/WorkspaceSettings.xcsettings` | Shared workspace settings. |
| `project.xcworkspace/xcshareddata/swiftpm/Package.resolved` | SwiftPM package pins. |
| `xcshareddata/xcschemes/newsly.xcscheme` | Shared build/test scheme. |

## Integration points
- Source additions in the app, share extension, generated contracts, tests, assets, or fonts often need target membership updates here.
- Prefer XcodeBuildMCP for simulator build/run/test workflows when available.
