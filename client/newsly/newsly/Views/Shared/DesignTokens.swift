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
    // Terracotta surface colors — warm cream/charcoal palette
    static var surfacePrimary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.102, green: 0.094, blue: 0.082, alpha: 1.0)  // #1a1815
                : UIColor(red: 0.992, green: 0.976, blue: 0.957, alpha: 1.0)  // #fdf9f4
        })
    }
    static var surfaceSecondary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.141, green: 0.133, blue: 0.125, alpha: 1.0)  // #242220
                : UIColor(red: 1.0, green: 1.0, blue: 1.0, alpha: 1.0)        // #ffffff
        })
    }
    static var surfaceTertiary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.180, green: 0.169, blue: 0.157, alpha: 1.0)  // #2e2b28
                : UIColor(red: 0.969, green: 0.953, blue: 0.933, alpha: 1.0)  // #f7f3ee
        })
    }
    static var surfaceContainer: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.208, green: 0.196, blue: 0.188, alpha: 1.0)  // #353230
                : UIColor(red: 0.945, green: 0.929, blue: 0.910, alpha: 1.0)  // #f1ede8
        })
    }
    static var surfaceContainerHigh: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.239, green: 0.227, blue: 0.216, alpha: 1.0)  // #3d3a37
                : UIColor(red: 0.922, green: 0.910, blue: 0.890, alpha: 1.0)  // #ebe8e3
        })
    }
    static var surfaceContainerHighest: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.271, green: 0.259, blue: 0.251, alpha: 1.0)  // #454240
                : UIColor(red: 0.902, green: 0.886, blue: 0.867, alpha: 1.0)  // #e6e2dd
        })
    }

    // Accent colors — storm gray
    static var terracottaPrimary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.616, green: 0.631, blue: 0.667, alpha: 1.0)  // #9DA1AA
                : UIColor(red: 0.290, green: 0.306, blue: 0.341, alpha: 1.0)  // #4A4E57
        })
    }
    static var terracottaDark: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.529, green: 0.545, blue: 0.580, alpha: 1.0)  // #878B94
                : UIColor(red: 0.227, green: 0.243, blue: 0.278, alpha: 1.0)  // #3A3E47
        })
    }

    // Terracotta text colors
    static var onSurface: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.929, green: 0.925, blue: 0.910, alpha: 1.0)  // #edece8
                : UIColor(red: 0.110, green: 0.110, blue: 0.098, alpha: 1.0)  // #1c1c19
        })
    }
    static var onSurfaceSecondary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.639, green: 0.616, blue: 0.588, alpha: 1.0)  // #a39d96
                : UIColor(red: 0.302, green: 0.271, blue: 0.247, alpha: 1.0)  // #4d453f
        })
    }

    // Chat-specific colors — cool ink, matching storm gray palette
    static var chatUserBubble: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.302, green: 0.314, blue: 0.341, alpha: 1.0)  // #4D5057
                : UIColor(red: 0.200, green: 0.212, blue: 0.239, alpha: 1.0)  // #33363D
        })
    }
    static var chatAccent: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.667, green: 0.678, blue: 0.702, alpha: 1.0)  // #AAADB3
                : UIColor(red: 0.353, green: 0.365, blue: 0.392, alpha: 1.0)  // #5A5D64
        })
    }

    // Outline — cool neutral
    static var outlineVariant: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.263, green: 0.271, blue: 0.286, alpha: 1.0)  // #434549
                : UIColor(red: 0.776, green: 0.788, blue: 0.808, alpha: 1.0)  // #C6C9CE
        })
    }

    // Text colors (keep existing for backward compat)
    static var textPrimary: Color { Color(.label) }
    static var textSecondary: Color { Color(.secondaryLabel) }
    static var textTertiary: Color { Color(.tertiaryLabel) }

    // Border colors
    static var borderSubtle: Color { Color(.separator) }
    static var borderStrong: Color { Color(.opaqueSeparator) }

    // Status colors (Linear-style muted)
    static var statusActive: Color { Color.green.opacity(0.85) }
    static var statusInactive: Color { Color(.tertiaryLabel) }
    static var statusDestructive: Color { Color.red.opacity(0.85) }

    // Editorial colors (Discovery redesign) — adaptive for dark mode
    static var editorialText: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.93, green: 0.93, blue: 0.94, alpha: 1.0)   // #EDEDED
                : UIColor(red: 0.067, green: 0.067, blue: 0.067, alpha: 1.0) // #111111
        })
    }
    static var editorialSub: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.62, green: 0.62, blue: 0.65, alpha: 1.0)   // #9E9EA6
                : UIColor(red: 0.443, green: 0.443, blue: 0.478, alpha: 1.0) // #71717A
        })
    }
    static var editorialBorder: Color { Color(.systemGray5) }

    // Adaptive accent (topic badges, favorites)
    static var topicAccent: Color {
        Color(UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor(red: 0.40, green: 0.61, blue: 1.0, alpha: 1.0)   // #669CFF brighter for dark
                : UIColor(red: 0.067, green: 0.322, blue: 0.831, alpha: 1.0) // #1152d4 original for light
        })
    }

    // Platform label color (news feed metadata — muted blue, related to topicAccent family)
    static var platformLabel: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.55, green: 0.70, blue: 0.95, alpha: 1.0)  // #8CB3F2
                : UIColor(red: 0.20, green: 0.40, blue: 0.70, alpha: 1.0)  // #3366B3
        })
    }

    // Day section delimiter text (distinct grey, not textTertiary)
    static var sectionDelimiter: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.50, green: 0.50, blue: 0.53, alpha: 1.0)  // #808087
                : UIColor(red: 0.45, green: 0.45, blue: 0.48, alpha: 1.0)  // #73737A
        })
    }

    // Earthy palette (Live Voice) — adaptive for dark mode
    static var earthTerracotta: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.831, green: 0.514, blue: 0.416, alpha: 1.0)  // #D4836A
                : UIColor(red: 0.765, green: 0.420, blue: 0.310, alpha: 1.0)  // #C36B4F
        })
    }
    static var earthSage: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.616, green: 0.678, blue: 0.431, alpha: 1.0)  // #9DAD6E
                : UIColor(red: 0.541, green: 0.604, blue: 0.357, alpha: 1.0)  // #8A9A5B
        })
    }
    static var earthIvory: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.102, green: 0.098, blue: 0.090, alpha: 1.0)  // #1A1917
                : UIColor(red: 0.976, green: 0.969, blue: 0.949, alpha: 1.0)  // #F9F7F2
        })
    }
    static var earthClayMuted: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.239, green: 0.208, blue: 0.188, alpha: 1.0)  // #3D3530
                : UIColor(red: 0.898, green: 0.827, blue: 0.773, alpha: 1.0)  // #E5D3C5
        })
    }
    static var earthStoneDark: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.710, green: 0.686, blue: 0.659, alpha: 1.0)  // #B5AFA8
                : UIColor(red: 0.365, green: 0.341, blue: 0.322, alpha: 1.0)  // #5D5752
        })
    }
    static var earthWoodWarm: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.651, green: 0.478, blue: 0.333, alpha: 1.0)  // #A67A55
                : UIColor(red: 0.545, green: 0.369, blue: 0.235, alpha: 1.0)  // #8B5E3C
        })
    }

    // Watercolor palette (Landing & Onboarding)
    static var watercolorBase: Color { Color(red: 0.973, green: 0.980, blue: 0.988) }           // #f8fafc
    static var watercolorMistyBlue: Color { Color(red: 0.580, green: 0.680, blue: 0.820) }      // #94ADD1
    static var watercolorDiffusedPeach: Color { Color(red: 0.960, green: 0.620, blue: 0.580) }   // #F59E94
    static var watercolorPaleEmerald: Color { Color(red: 0.400, green: 0.820, blue: 0.640) }     // #66D1A3
    static var watercolorSoftSky: Color { Color(red: 0.500, green: 0.780, blue: 0.960) }         // #80C7F5
    static var watercolorSlate: Color { Color(red: 0.200, green: 0.255, blue: 0.333) }           // #334155
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
    static let terracottaHeadlineItalic = Font.custom("Newsreader-Italic", size: 18)

    // Terracotta typography — Inter (sans-serif) for body/labels/UI
    static let terracottaBodyLarge = Font.custom("Inter", size: 16)
    static let terracottaBodyMedium = Font.custom("Inter", size: 14)
    static let terracottaBodySmall = Font.custom("Inter", size: 12)
    static let terracottaLabelSmall = Font.custom("Inter", size: 9).weight(.bold)
    static let terracottaCategoryPill = Font.custom("Inter", size: 10).weight(.semibold)
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
        UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.616, green: 0.631, blue: 0.667, alpha: 1.0)  // #9DA1AA
                : UIColor(red: 0.290, green: 0.306, blue: 0.341, alpha: 1.0)  // #4A4E57
        }
    }
    static var appOnSurfaceSecondary: UIColor {
        UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.639, green: 0.616, blue: 0.588, alpha: 1.0)  // #a39d96
                : UIColor(red: 0.302, green: 0.271, blue: 0.247, alpha: 1.0)  // #4d453f
        }
    }
    static var appSurfacePrimary: UIColor {
        UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.102, green: 0.094, blue: 0.082, alpha: 1.0)  // #1a1815
                : UIColor(red: 0.992, green: 0.976, blue: 0.957, alpha: 1.0)  // #fdf9f4
        }
    }
}
