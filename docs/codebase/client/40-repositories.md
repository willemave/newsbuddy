# client/newsly/newsly/Repositories/

Source folder: `client/newsly/newsly/Repositories`

## Purpose
Thin repository layer for shared read-state mutations used by view models.

## Runtime behavior
- `ReadStatusRepository` centralizes read/unread endpoint selection and calls for long-form content and news items.

## Important files
| File | Purpose |
|---|---|
| `ReadStatusRepository.swift` | Read-status endpoint routing and mutations. |

## Integration points
- View models call the owning content services directly and use the repository only for shared read-state routing.
- Network transport stays in `Services/APIClient.swift` and endpoint construction stays in `Services/APIEndpoints.swift`.
