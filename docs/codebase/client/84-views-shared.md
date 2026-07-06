# client/newsly/newsly/Views/Shared/

Source folder: `client/newsly/newsly/Views/Shared`

## Purpose
Cross-feature presentation primitives and design tokens used by many SwiftUI screens.

## Runtime behavior
- Shared view primitives provide consistent cards, badges, chips, section headers, dividers, state/loading states, settings rows, source rows, and backgrounds.
- `DesignTokens` centralizes visual constants plus shared `AppMotion` and `ShadowStyle` tokens for SwiftUI views.
- `PressableButtonStyle` and editorial/onboarding button styles use the 0.96 pressed scale with `AppMotion.press`.
- `SkeletonRow`, `SkeletonCard`, and feed/detail skeleton containers provide redacted-pulse initial loading states for the main content surfaces.
- `PaginationScrollTrigger` centralizes the 80% scroll-depth pagination trigger used by feed and secondary list surfaces.
- `LaneStatusRow` uses shared motion tokens for status changes and disables shimmer/pulse movement when Reduce Motion is enabled.
- Knowledge/source/status helpers keep repeated icons and metadata rows out of feature screens.

## Important files
| File | Purpose |
|---|---|
| `DesignTokens.swift` | Shared visual, motion, and shadow tokens. |
| `ReadStateCache.swift` | Shared optimistic read-state cache used by feed lists and detail screens. |
| `AddButton.swift`, `AppBadge.swift`, `StatusChip.swift`, `KnowledgeSaveIcon.swift` | Small action/status/icon primitives. |
| `PressableButtonStyle.swift`, `EditorialCardButtonStyle.swift` | Shared press feedback using `AppMotion.press`. |
| `GlassCard.swift`, `SettingsRow.swift`, `SourceRow.swift`, `SourceVisualMetadata.swift` | Reusable row/card/presentation helpers. |
| `EmptyStateView.swift`, `LoadingOverlay.swift`, `LaneStatusRow.swift` | Shared `StateView(role:)`, empty/error wrappers, and loading/status states. |
| `SkeletonViews.swift` | Redacted skeleton rows/cards for feed and detail initial loads. |
| `PaginationScrollTrigger.swift` | Shared `onPaginationThresholdReached` modifier for scroll-depth pagination. |
| `SearchBar.swift`, `SectionDivider.swift`, `SectionHeader.swift` | Common list/section controls. |
| `OnboardingSuggestionCard.swift` | Shared onboarding/discovery suggestion card. |
| `WatercolorBackground.swift` | Shared branded background primitive. |

## Integration points
- Feature-specific views should reuse these primitives before inventing local variants.
