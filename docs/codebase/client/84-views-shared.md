# client/newsly/newsly/Views/Shared/

Source folder: `client/newsly/newsly/Views/Shared`

## Purpose
Cross-feature presentation primitives and design tokens such as cards, chips, headers, dividers, search bars, and branded backgrounds.

## Runtime behavior
- Defines the shared visual language for reusable rows, labels, status chips, and decorative surfaces.
- Keeps common styling and structural components out of feature screens so layout and branding stay consistent.

## Inventory scope
- Direct file inventory for `client/newsly/newsly/Views/Shared`.

## Modules and files
| File | Key symbols | Notes |
|---|---|---|
| `client/newsly/newsly/Views/Shared/AddButton.swift` | `struct AddButton`, `glassButtonIfAvailable` | Types: `struct AddButton`. Functions: `glassButtonIfAvailable` |
| `client/newsly/newsly/Views/Shared/AppBadge.swift` | `struct CountBadge`, `struct TextBadge`, `enum Style` | Numeric count badge (e.g |
| `client/newsly/newsly/Views/Shared/DesignTokens.swift` | `enum CardMetrics`, `enum AppTextSize`, `enum ContentTextSize`, `enum Spacing`, `enum RowMetrics`, `enum AppRowFamily`, `appListRow`, `appRow`, `screenContainer` | Default horizontal padding for rows and screen content (20pt baseline). |
| `client/newsly/newsly/Views/Shared/EmptyStateView.swift` | `struct EmptyStateView` | Backward-compatible alias. |
| `client/newsly/newsly/Views/Shared/GlassCard.swift` | `struct GlassCardModifier`, `body`, `glassCard` | Types: `struct GlassCardModifier`. Functions: `body`, `glassCard` |
| `client/newsly/newsly/Views/Shared/LaneStatusRow.swift` | `struct LaneStatusRow` | Types: `struct LaneStatusRow` |
| `client/newsly/newsly/Views/Shared/LoadingOverlay.swift` | `struct LoadingOverlay` | Types: `struct LoadingOverlay` |
| `client/newsly/newsly/Views/Shared/OnboardingSuggestionCard.swift` | `struct OnboardingSuggestionCard` | Types: `struct OnboardingSuggestionCard` |
| `client/newsly/newsly/Views/Shared/SearchBar.swift` | `struct SearchBar` | Types: `struct SearchBar` |
| `client/newsly/newsly/Views/Shared/SectionDivider.swift` | `struct SectionDivider`, `struct RowDivider` | Types: `struct SectionDivider`, `struct RowDivider` |
| `client/newsly/newsly/Views/Shared/SectionHeader.swift` | `struct SectionHeader` | Types: `struct SectionHeader` |
| `client/newsly/newsly/Views/Shared/SettingsRow.swift` | `struct SettingsRow`, `struct NavigationChevron`, `struct SettingsToggleRow` | Types: `struct SettingsRow`, `struct NavigationChevron`, `struct SettingsToggleRow` |
| `client/newsly/newsly/Views/Shared/SourceRow.swift` | `struct SourceRow`, `struct SourceTypeIcon` | Types: `struct SourceRow`, `struct SourceTypeIcon` |
| `client/newsly/newsly/Views/Shared/StatusChip.swift` | `struct StatusChip` | Types: `struct StatusChip` |
| `client/newsly/newsly/Views/Shared/WatercolorBackground.swift` | `struct WatercolorBackground`, `titleGlowColor`, `titleOscillation` | Returns a subtle vertical offset (~+/-3pt) synced with blob 0's sine phase. |
