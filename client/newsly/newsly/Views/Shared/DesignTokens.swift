//
//  DesignTokens.swift
//  newsly
//
//  Design system tokens for consistent styling across the app.
//

import SwiftUI
import UIKit

// MARK: - Colors

extension Color {
    // Editorial surface colors - selected reader palette.
    static var surfacePrimary: Color {
        Color(UIColor.appSurfacePrimary)
    }
    static var surfaceSecondary: Color {
        Color(ReaderPalette.selectedUIColor(\.surfaceSecondary))
    }
    static var surfaceTertiary: Color {
        Color(ReaderPalette.selectedUIColor(\.surfaceTertiary))
    }
    static var surfaceContainer: Color {
        Color(ReaderPalette.selectedUIColor(\.surfaceContainer))
    }
    static var surfaceContainerHigh: Color {
        Color(ReaderPalette.selectedUIColor(\.surfaceContainerHigh))
    }
    static var surfaceContainerHighest: Color {
        Color(ReaderPalette.selectedUIColor(\.surfaceContainerHighest))
    }

    // Palette roles.
    // Primary: active controls, date labels, audio/play affordances.
    static var brandPrimary: Color {
        Color(UIColor.appAccent)
    }
    static var brandPrimaryStrong: Color {
        Color(ReaderPalette.selectedUIColor(\.brandPrimaryStrong))
    }
    // Secondary: metadata, sources, categories, informational emphasis.
    static var brandSecondary: Color {
        Color(UIColor.appSecondaryAccent)
    }
    // Tertiary: saved/read states, unread markers, quiet dividers.
    static var brandTertiary: Color {
        Color(UIColor.appTertiaryAccent)
    }

    // Backward-compatible accent aliases.
    static var terracottaPrimary: Color { Color.brandPrimary }
    static var terracottaDark: Color { Color.brandPrimaryStrong }

    // Editorial text colors.
    static var onSurface: Color {
        Color(UIColor.appOnSurface)
    }
    static var onSurfaceSecondary: Color {
        Color(UIColor.appOnSurfaceSecondary)
    }
    static var onSurfaceTertiary: Color {
        Color(UIColor.appOnSurfaceTertiary)
    }

    // Chat-specific colors.
    static var chatUserBubble: Color {
        Color(ReaderPalette.selectedUIColor(\.chatUserBubble))
    }
    static var chatAccent: Color {
        Color.brandPrimary
    }

    // Outline - low-contrast linework.
    static var outlineVariant: Color {
        Color(ReaderPalette.selectedUIColor(\.outlineVariant))
    }

    // Backward-compatible text aliases.
    static var textPrimary: Color { Color.onSurface }
    static var textSecondary: Color { Color.onSurfaceSecondary }
    static var textTertiary: Color { Color.onSurfaceTertiary }

    // Border colors
    static var borderSubtle: Color {
        Color(ReaderPalette.selectedUIColor(\.borderSubtle))
    }
    static var borderStrong: Color {
        Color(ReaderPalette.selectedUIColor(\.borderStrong))
    }

    // Status colors (reader-palette muted)
    static var statusSuccess: Color { Color.brandSecondary.opacity(0.9) }
    static var statusProcessing: Color { Color.brandPrimary }
    static var statusActive: Color { Color.statusSuccess }
    static var statusInactive: Color { Color.onSurfaceTertiary }
    static var statusDestructive: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.890, green: 0.467, blue: 0.467, alpha: 1.0)  // #e37777
                : UIColor(red: 0.686, green: 0.208, blue: 0.208, alpha: 1.0)  // #af3535
        })
    }

    // Editorial colors (Discovery redesign) — adaptive for dark mode
    static var editorialText: Color { Color.onSurface }
    static var editorialSub: Color { Color.onSurfaceSecondary }
    static var editorialBorder: Color { Color.outlineVariant }

    // Adaptive accent (topic badges, knowledge saves)
    static var topicAccent: Color { Color.brandSecondary }

    // Platform label color (news feed metadata)
    static var platformLabel: Color { Color.brandSecondary }

    // Day section delimiter text (distinct grey, not textTertiary)
    static var sectionDelimiter: Color { Color.brandTertiary }

    // Summary and artifact accents.
    static var summaryPrimaryAccent: Color { Color.brandPrimary }
    static var summarySecondaryAccent: Color { Color.brandSecondary }
    static var summaryQuestionAccent: Color { Color.brandPrimaryStrong }
    static var summaryCounterpointAccent: Color { Color.brandTertiary }
    static var summaryQuoteAccent: Color { Color.brandPrimaryStrong }

    // Onboarding and ambient illustration roles.
    static var onboardingSurface: Color { Color.surfacePrimary }
    static var onboardingText: Color { Color.onSurface }
    static var onboardingSelectionAccent: Color { Color.brandSecondary }
    static var onboardingAmbientPrimary: Color { Color.brandSecondary }
    static var onboardingAmbientSecondary: Color { Color.brandPrimary.opacity(0.92) }
    static var onboardingAmbientTertiary: Color { Color.brandTertiary }
    static var onboardingAmbientQuaternary: Color { Color.brandSecondary.opacity(0.86) }
}

// MARK: - Typography

extension Font {
    static let listTitle = Font.body
    static let listSubtitle = Font.subheadline
    static let listCaption = Font.caption
    static let listMono = Font.system(.caption, design: .monospaced)

    static let sectionHeader = Font.footnote.weight(.semibold)
    static let chipLabel = Font.caption2.weight(.medium)

    // Feed card typography
    static let feedMeta = Font.system(size: 11, weight: .regular)
    static let feedHeadline = Font.system(size: 18, weight: .regular)
    static let feedSnippet = Font.system(size: 13)
    static let cardHeadline = Font.system(size: 22, weight: .bold)
    static let cardDescription = Font.system(size: 14)
    static let cardBadge = Font.system(size: 10, weight: .semibold)
    static let cardFooter = Font.system(size: 11, weight: .medium)

    // Editorial typography (Discovery redesign)
    static let editorialDisplay = Font.system(.largeTitle, design: .serif)
    static let editorialHeadline = Font.system(.title3, design: .serif)
    static let editorialBody = Font.system(.body, design: .serif)
    static let editorialMeta = Font.caption2.weight(.bold)
    static let editorialSubMeta = Font.caption2

    // Watercolor typography (Landing & Onboarding)
    static let watercolorDisplay = Font.system(size: 54, weight: .regular, design: .serif)
    static let watercolorSubtitle = Font.system(size: 17, weight: .light)

    // Terracotta typography — Newsreader (serif) for headlines/display
    static let terracottaDisplayLarge = Font.custom("Newsreader", size: 44)
    static let terracottaHeadlineLarge = Font.custom("Newsreader", size: 28)
    static let terracottaHeadlineMedium = Font.custom("Newsreader", size: 22).weight(.semibold)
    static let terracottaHeadlineSmall = Font.custom("Newsreader", size: 18)
    static let terracottaHeadlineCompact = Font.custom("Newsreader", size: 22)
    static let terracottaHeadlineItalic = Font.custom("Newsreader-Italic", size: 18)

    // Terracotta typography — Inter (sans-serif) for body/labels/UI
    static let terracottaBodyLarge = Font.custom("Inter", size: 16)
    static let terracottaBodyMedium = Font.custom("Inter", size: 14)
    static let terracottaBodySmall = Font.custom("Inter", size: 12)
    static let terracottaLabelSmall = Font.custom("Inter", size: 9).weight(.bold)
    static let terracottaCategoryPill = Font.custom("Inter", size: 10).weight(.semibold)
}

extension UIFont {
    static var appTerracottaHeadlineCompact: UIFont {
        UIFont(name: "Newsreader", size: 22) ?? UIFont.systemFont(ofSize: 22, weight: .regular)
    }

    static var appEditorialHeadline: UIFont {
        UIFont(name: "Newsreader", size: 28) ?? UIFont.systemFont(ofSize: 28, weight: .regular)
    }

    static var appEditorialSummary: UIFont {
        UIFont(name: "Inter", size: 14) ?? UIFont.systemFont(ofSize: 14, weight: .regular)
    }
}

// MARK: - Card Metrics

enum CardMetrics {
    static let heroImageHeight: CGFloat = 180
    static let cardCornerRadius: CGFloat = 24
    static let cardSpacing: CGFloat = 20
    static let textOverlapOffset: CGFloat = -40
}

// MARK: - Text Size

enum AppTextSize: Int, CaseIterable {
    case small = 0
    case standard = 1
    case large = 2
    case extraLarge = 3

    var label: String {
        switch self {
        case .small: return "Small"
        case .standard: return "Standard"
        case .large: return "Large"
        case .extraLarge: return "Extra Large"
        }
    }

    var dynamicTypeSize: DynamicTypeSize {
        switch self {
        case .small: return .small
        case .standard: return .large
        case .large: return .xLarge
        case .extraLarge: return .xxLarge
        }
    }

    init(index: Int) {
        self = AppTextSize(rawValue: index) ?? .standard
    }
}

enum ContentTextSize: Int, CaseIterable {
    case small = 0
    case standard = 1
    case medium = 2
    case large = 3
    case extraLarge = 4

    var label: String {
        switch self {
        case .small: return "Small"
        case .standard: return "Standard"
        case .medium: return "Medium"
        case .large: return "Large"
        case .extraLarge: return "Extra Large"
        }
    }

    var dynamicTypeSize: DynamicTypeSize {
        switch self {
        case .small: return .small
        case .standard: return .large
        case .medium: return .xLarge
        case .large: return .xxLarge
        case .extraLarge: return .xxxLarge
        }
    }

    init(index: Int) {
        self = ContentTextSize(rawValue: index) ?? .medium
    }
}

// MARK: - Spacing

enum Spacing {
    /// Default horizontal padding for rows and screen content (20pt baseline).
    static let screenHorizontal: CGFloat = 20
    static let rowHorizontal: CGFloat = 20
    static let rowVertical: CGFloat = 12
    static let sectionTop: CGFloat = 24
    static let sectionBottom: CGFloat = 8
    static let iconSize: CGFloat = 28
    static let smallIcon: CGFloat = 20

    /// Leading inset for row dividers (aligns with text after icon + spacing).
    static let rowDividerInset: CGFloat = rowHorizontal + iconSize + 12
}

// MARK: - Row Metrics

/// Two row families: compact (settings/menus) and regular (content cards).
enum RowMetrics {
    /// Compact rows: settings, menu items, simple navigation (44pt).
    static let compactHeight: CGFloat = 44
    /// Regular rows: content cards, rich list items (76pt).
    static let regularHeight: CGFloat = 76
    /// Thumbnail size for regular rows.
    static let thumbnailSize: CGFloat = 60
    /// Small thumbnail/icon container for compact rows.
    static let smallThumbnailSize: CGFloat = 40
}

// MARK: - Row Family

enum AppRowFamily {
    case compact
    case regular
}

// MARK: - View Modifiers

extension View {
    /// Apply standard row padding and minimum height for a given row family.
    func appRow(_ family: AppRowFamily = .regular) -> some View {
        self
            .padding(.horizontal, Spacing.rowHorizontal)
            .padding(.vertical, Spacing.rowVertical)
            .frame(
                minHeight: family == .compact
                    ? RowMetrics.compactHeight
                    : RowMetrics.regularHeight,
                alignment: .center
            )
            .contentShape(Rectangle())
    }

    /// Standard List row configuration: zero insets (let the row handle padding),
    /// hidden separators, and clear background.
    func appListRow() -> some View {
        self
            .listRowInsets(EdgeInsets())
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
    }

    /// Apply standard screen-level background.
    func screenContainer() -> some View {
        self.background(Color.surfacePrimary)
    }
}

// MARK: - UIColor Design Tokens (for UIKit appearance APIs)

extension UIColor {
    static var appAccent: UIColor {
        ReaderPalette.selectedUIColor(\.brandPrimary)
    }
    static var appSecondaryAccent: UIColor {
        ReaderPalette.selectedUIColor(\.brandSecondary)
    }
    static var appTertiaryAccent: UIColor {
        ReaderPalette.selectedUIColor(\.brandTertiary)
    }
    static var appOnSurface: UIColor {
        ReaderPalette.selectedUIColor(\.onSurface)
    }
    static var appOnSurfaceSecondary: UIColor {
        ReaderPalette.selectedUIColor(\.onSurfaceSecondary)
    }
    static var appOnSurfaceTertiary: UIColor {
        ReaderPalette.selectedUIColor(\.onSurfaceTertiary)
    }
    static var appSurfacePrimary: UIColor {
        ReaderPalette.selectedUIColor(\.surfacePrimary)
    }
}
