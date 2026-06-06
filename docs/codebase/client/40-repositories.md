# client/newsly/newsly/Repositories/

Source folder: `client/newsly/newsly/Repositories`

## Purpose
Thin repository layer that wraps lower-level service/API calls for content and read-state workflows used by view models.

## Runtime behavior
- `ContentRepository` exposes async content list/detail/search/action helpers over `ContentService`.
- `ReadStatusRepository` centralizes read/unread endpoint selection and calls for long-form content and news items.

## Important files
| File | Purpose |
|---|---|
| `ContentRepository.swift` | Content list/detail/action repository methods. |
| `ReadStatusRepository.swift` | Read-status endpoint routing and mutations. |

## Integration points
- View models depend on repositories where they need higher-level content operations.
- Network transport stays in `Services/APIClient.swift` and endpoint construction stays in `Services/APIEndpoints.swift`.
