# client/newsly/

Source folder: `client/newsly`

## Purpose
Xcode workspace root for the Newsly iOS app, share extension, generated OpenAPI artifacts, client scripts, tests, and local configuration templates.

## Runtime behavior
- `newsly.xcodeproj` contains the app and share extension targets.
- `newsly.xcconfig` is shared app configuration.
- `Secrets.xcconfig.template` plus `sync-secrets.sh` define local secret sync; `Secrets.xcconfig` is local machine state and should not be treated as durable documentation.
- `openapi-generator-config.yaml` controls Swift OpenAPI generation.
- `client/newsly/scripts/regenerate_api_contracts.sh` regenerates backend OpenAPI, handwritten Swift enum contracts, and Swift OpenAPI generated client/types.

## Important files and folders
| Path | Purpose |
|---|---|
| `.gitignore` | Client-local ignore rules. |
| `newsly.xcodeproj/` | Xcode project, schemes, workspace metadata, and package resolution. |
| `newsly/` | Main SwiftUI app target. |
| `ShareExtension/` | iOS share extension target. |
| `OpenAPI/Generated/` | Generated Swift OpenAPI client/types. |
| `newslyTests/` | Unit tests. |
| `newslyUITests/` | UI tests. |
| `scripts/` | Client helper scripts. |
| `newsly.xcconfig` | Shared app build configuration. |
| `Secrets.xcconfig.template` | Secret template. |
| `sync-secrets.sh` | Copies/syncs local secrets into the expected xcconfig file. |
| `openapi-generator-config.yaml` | Swift OpenAPI generator config. |

## Excluded local files
Local DBs, build products, `xcuserdata`, and generated user-interface state are runtime/local artifacts.
