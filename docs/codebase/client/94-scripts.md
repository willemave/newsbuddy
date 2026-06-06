# client/newsly/scripts/

Source folder: `client/newsly/scripts`

## Purpose
Client-local helper scripts, currently focused on regenerating API contract artifacts.

## Runtime behavior
- `regenerate_api_contracts.sh` runs from the repo root.
- It exports backend OpenAPI with `scripts/export_openapi_schema.py`.
- It regenerates lightweight Swift API contracts with `scripts/generate_ios_contracts.py`.
- It regenerates Swift OpenAPI client/types with `scripts/generate_ios_openapi_artifacts.sh`.

## Important files
| File | Purpose |
|---|---|
| `regenerate_api_contracts.sh` | Full iOS OpenAPI/contract regeneration flow. |

## Integration points
- Generated outputs land in `client/newsly/newsly/Models/Generated` and `client/newsly/OpenAPI/Generated`.
- Run after backend API contract changes.
