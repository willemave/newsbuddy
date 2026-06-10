# Generated Client Contracts

Source folder: `client/newsly/newsly/Models/Generated`

## Purpose
Checked-in generated Swift contract artifacts synchronized from backend OpenAPI/export scripts.

## Runtime behavior
- `Models/Generated/APIContracts.generated.swift` contains lightweight generated enums and API contract types from `scripts/generate_ios_contracts.py`.
- `client/newsly/scripts/regenerate_api_contracts.sh` exports backend OpenAPI and regenerates the lightweight Swift contracts.

## Important files
| File | Purpose |
|---|---|
| `client/newsly/newsly/Models/Generated/APIContracts.generated.swift` | Generated lightweight Swift API contracts. |
| `client/newsly/scripts/regenerate_api_contracts.sh` | Regeneration command. |

## Notes
- Do not edit generated files manually.
- Regenerate after backend API contract changes and verify the iOS target still builds.
