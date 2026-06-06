# client/newsly/newsly/Shared/

Source folder: `client/newsly/newsly/Shared`

## Purpose
Shared observable state, dependency factories, app chrome, reader palette, and app-group container helpers reused across tabs, detail flows, onboarding, and the share extension.

## Runtime behavior
- `AppChrome` and `RootDependencyFactory` centralize root dependency setup and app-level chrome state.
- `ReaderPalette` stores and applies reader color/theme preferences.
- `OnboardingStateStore` and `ReadingStateStore` persist cross-screen state.
- `SharedContainer` resolves app-group/shared-container storage for the app and share extension.

## Important files
| File | Purpose |
|---|---|
| `AppChrome.swift` | App chrome state and root dependency factory. |
| `ReaderPalette.swift` | Reader palette definitions and persistence. |
| `OnboardingStateStore.swift` | Onboarding/tutorial state persistence. |
| `ReadingStateStore.swift` | Shared reading/read-state store. |
| `SharedContainer.swift` | App-group container helpers. |

## Integration points
- Root setup in `newslyApp.swift` and `ContentView.swift` injects shared state into views.
- Settings screens mutate reader/app preferences through shared stores.
