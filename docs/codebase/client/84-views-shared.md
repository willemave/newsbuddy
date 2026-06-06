# client/newsly/newsly/Views/Shared/

Source folder: `client/newsly/newsly/Views/Shared`

## Purpose
Cross-feature presentation primitives and design tokens used by many SwiftUI screens.

## Runtime behavior
- Shared view primitives provide consistent cards, badges, chips, section headers, dividers, empty/loading states, settings rows, source rows, and backgrounds.
- `DesignTokens` centralizes visual constants for SwiftUI views.
- Knowledge/source/status helpers keep repeated icons and metadata rows out of feature screens.

## Important files
| File | Purpose |
|---|---|
| `DesignTokens.swift` | Shared visual tokens. |
| `AddButton.swift`, `AppBadge.swift`, `StatusChip.swift`, `KnowledgeSaveIcon.swift` | Small action/status/icon primitives. |
| `GlassCard.swift`, `SettingsRow.swift`, `SourceRow.swift`, `SourceVisualMetadata.swift` | Reusable row/card/presentation helpers. |
| `EmptyStateView.swift`, `LoadingOverlay.swift`, `LaneStatusRow.swift` | Empty/loading/status states. |
| `SearchBar.swift`, `SectionDivider.swift`, `SectionHeader.swift` | Common list/section controls. |
| `OnboardingSuggestionCard.swift` | Shared onboarding/discovery suggestion card. |
| `WatercolorBackground.swift` | Shared branded background primitive. |

## Integration points
- Feature-specific views should reuse these primitives before inventing local variants.
