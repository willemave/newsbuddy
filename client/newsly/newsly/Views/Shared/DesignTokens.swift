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
    // Fixed platform chrome tint. Reader palettes recolor content, not navigation selection.
    static var appChromeAccent: Color {
        Color(UIColor.appChromeAccent)
    }

    // Backward-compatible accent aliases.
    static var terracottaPrimary: Color { Color.brandPrimary }
    static var terracottaDark: Color { Color.brandPrimaryStrong }

    // Editorial text colors.
    static var onSurface: Color {
        Color(UIColor.appOnSurface)
    }
    static var readerBodyText: Color {
        Color(UIColor.appReaderBodyText)
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
    static var statusSuccess: Color { Color.brandPrimary }
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

    // Adaptive accent (topic badges, knowledge saves) — neutral metadata, not a hue
    static var topicAccent: Color { Color.onSurfaceSecondary }

    // Platform label color (news feed metadata)
    static var platformLabel: Color { Color.onSurfaceSecondary }

    // Day section delimiter text (quiet grey)
    static var sectionDelimiter: Color { Color.onSurfaceTertiary }

    // Summary and artifact accents — single accent; section text is neutralized at call sites.
    static var summaryPrimaryAccent: Color { Color.brandPrimary }
    static var summarySecondaryAccent: Color { Color.brandPrimary }
    static var summaryQuestionAccent: Color { Color.brandPrimary }
    static var summaryCounterpointAccent: Color { Color.brandPrimary }
    static var summaryQuoteAccent: Color { Color.brandPrimary }

    // Onboarding and ambient illustration roles.
    // Keep this independent from reader palettes so the first-run flow stays colorful.
    static var onboardingSurface: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.047, green: 0.059, blue: 0.078, alpha: 1.0)  // #0c0f14
                : UIColor(red: 0.973, green: 0.980, blue: 0.988, alpha: 1.0)  // #f8fafc
        })
    }
    static var onboardingText: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.886, green: 0.910, blue: 0.941, alpha: 1.0)  // #e2e8f0
                : UIColor(red: 0.200, green: 0.255, blue: 0.333, alpha: 1.0)  // #334155
        })
    }
    static var onboardingSelectionAccent: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.165, green: 0.478, blue: 0.322, alpha: 1.0)  // #2a7a52
                : UIColor(red: 0.400, green: 0.820, blue: 0.640, alpha: 1.0)  // #66d1a3
        })
    }
    static var onboardingAmbientPrimary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.227, green: 0.353, blue: 0.549, alpha: 1.0)  // #3a5a8c
                : UIColor(red: 0.580, green: 0.680, blue: 0.820, alpha: 1.0)  // #94add1
        })
    }
    static var onboardingAmbientSecondary: Color { Color.onboardingAmbientTertiary }
    static var onboardingAmbientTertiary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.549, green: 0.290, blue: 0.259, alpha: 1.0)  // #8c4a42
                : UIColor(red: 0.960, green: 0.620, blue: 0.580, alpha: 1.0)  // #f59e94
        })
    }
    static var onboardingAmbientQuaternary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.165, green: 0.416, blue: 0.612, alpha: 1.0)  // #2a6a9c
                : UIColor(red: 0.500, green: 0.780, blue: 0.960, alpha: 1.0)  // #80c7f5
        })
    }
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

    // Reader typography — regular body copy.
    static let readerBody = Font.custom("Inter", size: ReaderContentStyle.bodyFontSize)
        .weight(ReaderContentStyle.bodyFontWeight)
    static let readerSummaryBody = Font.custom("Inter", size: ReaderContentStyle.summaryBodyFontSize)
        .weight(ReaderContentStyle.bodyFontWeight)
}

extension UIFont {
    static var appTerracottaHeadlineCompact: UIFont {
        UIFont(name: "Newsreader", size: 22) ?? UIFont.systemFont(ofSize: 22, weight: .regular)
    }

    static var appEditorialHeadline: UIFont {
        UIFont(name: "Newsreader", size: 28) ?? UIFont.systemFont(ofSize: 28, weight: .regular)
    }

    static var appEditorialSummary: UIFont {
        UIFont(name: "Inter", size: ReaderContentStyle.summaryBodyFontSize)
            ?? UIFont.systemFont(
                ofSize: ReaderContentStyle.summaryBodyFontSize,
                weight: ReaderContentStyle.uiBodyFontWeight
            )
    }

    static var appReaderBody: UIFont {
        UIFont(name: "Inter", size: ReaderContentStyle.bodyFontSize)
            ?? UIFont.systemFont(
                ofSize: ReaderContentStyle.bodyFontSize,
                weight: ReaderContentStyle.uiBodyFontWeight
            )
    }
}

enum ReaderContentStyle {
    static let bodyTextOpacity: CGFloat = 1.0
    static let bodyFontWeight: Font.Weight = .regular
    static let uiBodyFontWeight: UIFont.Weight = .regular
    static let bodyFontSize: CGFloat = 15
    static let summaryBodyFontSize: CGFloat = 14
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
    /// Reader content gutter for long-form/detail reading surfaces.
    static let readerHorizontal: CGFloat = 36
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
    static var appChromeAccent: UIColor {
        // Muted neutral nav selection (palette-independent, same across all themes).
        .secondaryLabel
    }
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
    static var appReaderBodyText: UIColor {
        UIColor { traitCollection in
            appOnSurface
                .resolvedColor(with: traitCollection)
                .withAlphaComponent(ReaderContentStyle.bodyTextOpacity)
        }
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
