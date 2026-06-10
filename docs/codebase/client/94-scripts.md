# client/newsly/scripts/

Source folder: `client/newsly/scripts`

## Purpose
Client-local helper scripts, currently focused on regenerating API contract artifacts.

## Runtime behavior
- `regenerate_api_contracts.sh` runs from the repo root.
- It exports backend OpenAPI with `scripts/export_openapi_schema.py`.
- It regenerates lightweight Swift API contracts with `scripts/generate_ios_contracts.py`.

## Important files
| File | Purpose |
|---|---|
| `regenerate_api_contracts.sh` | iOS OpenAPI schema and enum contract regeneration flow. |

## Integration points
- Generated Swift outputs land in `client/newsly/newsly/Models/Generated`.
- Run after backend API contract changes.
