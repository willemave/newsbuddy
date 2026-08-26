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
    static var chatUserBubbleText: Color {
        Color.onSurface
    }
    static var chatAccent: Color {
        Color.brandPrimary
    }

    // Outline - low-contrast linework.
    static var outlineVariant: Color {
        Color(ReaderPalette.selectedUIColor(\.outlineVariant))
    }

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

    // Adaptive accent (topic badges, knowledge saves) — neutral metadata, not a hue
    static var topicAccent: Color { Color.onSurfaceSecondary }

    // Platform label color (news feed metadata)
    static var platformLabel: Color { Color.onSurfaceSecondary }

    // Day section delimiter text (quiet grey)
    static var sectionDelimiter: Color { Color.onSurfaceTertiary }

    // Onboarding and ambient illustration roles.
    // Aligned with the reader palette (charcoal/slate + single amber accent).
    // Separate tokens remain because onboarding paints ambient washes the
    // reader UI never uses.
    static var onboardingSurface: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.075, green: 0.082, blue: 0.098, alpha: 1.0)  // #131519
                : UIColor(red: 0.957, green: 0.961, blue: 0.969, alpha: 1.0)  // #f4f5f7
        })
    }
    static var onboardingText: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.898, green: 0.906, blue: 0.925, alpha: 1.0)  // #e5e7ec
                : UIColor(red: 0.106, green: 0.118, blue: 0.141, alpha: 1.0)  // #1b1e24
        })
    }
    static var onboardingSelectionAccent: Color {
        Color.brandPrimary
    }
    // Ambient wash ramp: amber, bronze, cream — one hue at three intensities.
    static var onboardingAmbientPrimary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.369, green: 0.282, blue: 0.125, alpha: 1.0)  // #5e4820
                : UIColor(red: 0.902, green: 0.773, blue: 0.518, alpha: 1.0)  // #e6c584
        })
    }
    static var onboardingAmbientTertiary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.478, green: 0.361, blue: 0.141, alpha: 1.0)  // #7a5c24
                : UIColor(red: 0.831, green: 0.659, blue: 0.384, alpha: 1.0)  // #d4a862
        })
    }
    static var onboardingAmbientQuaternary: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.235, green: 0.196, blue: 0.133, alpha: 1.0)  // #3c3222
                : UIColor(red: 0.925, green: 0.875, blue: 0.765, alpha: 1.0)  // #ecdfc3
        })
    }
    // Muted echo of the mascot's purple — the one cool note in the ambient wash.
    static var onboardingAmbientMascot: Color {
        Color(UIColor { tc in
            tc.userInterfaceStyle == .dark
                ? UIColor(red: 0.271, green: 0.216, blue: 0.427, alpha: 1.0)  // #45376d
                : UIColor(red: 0.812, green: 0.753, blue: 0.929, alpha: 1.0)  // #cfc0ed
        })
    }
}

// MARK: - Typography

enum AppFontFamily {
    static let sans = "Lato-Regular"
    static let sansItalic = "Lato-Italic"
    static let serif = "Lora-Regular"
    static let serifItalic = "Lora-Italic"
}

extension Font {
    static func appSans(
        size: CGFloat,
        relativeTo textStyle: Font.TextStyle = .body,
        weight: Font.Weight = .regular
    ) -> Font {
        Font.custom(AppFontFamily.sans, size: size, relativeTo: textStyle).weight(weight)
    }

    static func appSansItalic(
        size: CGFloat,
        relativeTo textStyle: Font.TextStyle = .body,
        weight: Font.Weight = .regular
    ) -> Font {
        Font.custom(AppFontFamily.sansItalic, size: size, relativeTo: textStyle).weight(weight)
    }

    static func appSerif(
        size: CGFloat,
        relativeTo textStyle: Font.TextStyle = .title,
        weight: Font.Weight = .regular
    ) -> Font {
        Font.custom(AppFontFamily.serif, size: size, relativeTo: textStyle).weight(weight)
    }

    static func appSerifItalic(
        size: CGFloat,
        relativeTo textStyle: Font.TextStyle = .body,
        weight: Font.Weight = .regular
    ) -> Font {
        Font.custom(AppFontFamily.serifItalic, size: size, relativeTo: textStyle).weight(weight)
    }

    static func appSymbol(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        Font.system(size: size, weight: weight)
    }

    static let appLargeTitle = Font.appSerif(size: 34, relativeTo: .largeTitle, weight: .semibold)
    static let appTitle = Font.appSerif(size: 28, relativeTo: .title, weight: .semibold)
    static let appTitle2 = Font.appSerif(size: 22, relativeTo: .title2, weight: .semibold)
    static let appTitle3 = Font.appSerif(size: 20, relativeTo: .title3, weight: .semibold)
    static let appHeadline = Font.appSerif(size: 17, relativeTo: .headline, weight: .semibold)
    static let appSubheadline = Font.appSans(size: 15, relativeTo: .subheadline)
    static let appBody = Font.appSans(size: 16, relativeTo: .body)
    static let appCallout = Font.appSans(size: 16, relativeTo: .callout)
    static let chatBody = Font.appSans(size: 13, relativeTo: .callout)
    static let appFootnote = Font.appSans(size: 13, relativeTo: .footnote)
    static let appCaption = Font.appSans(size: 12, relativeTo: .caption)
    static let appCaption2 = Font.appSans(size: 11, relativeTo: .caption2)

    static let listTitle = Font.appSans(size: 17, relativeTo: .body)
    static let listSubtitle = Font.appSans(size: 15, relativeTo: .subheadline)
    static let listCaption = Font.appSans(size: 12, relativeTo: .caption)
    static let listValue = Font.appCaption.monospacedDigit()

    static let sectionHeader = Font.appSans(size: 13, relativeTo: .footnote, weight: .semibold)
    static let chipLabel = Font.appSans(size: 11, relativeTo: .caption2, weight: .medium)

    // Editorial typography.
    static let editorialMeta = Font.appSans(size: 11, relativeTo: .caption2, weight: .bold)

    // Watercolor typography (Landing & Onboarding) follows the app title/body split.
    static let watercolorDisplay = Font.appSerif(size: 54, relativeTo: .largeTitle, weight: .semibold)
    static let watercolorSubtitle = Font.appSans(size: 17, relativeTo: .body)

    // Terracotta typography - title aliases use the serif family, body/labels use the sans family.
    static let terracottaDisplayLarge = Font.appSerif(size: 44, relativeTo: .largeTitle, weight: .semibold)
    static let terracottaHeadlineLarge = Font.appSerif(size: 28, relativeTo: .title, weight: .semibold)
    static let terracottaHeadlineMedium = Font.appSerif(size: 22, relativeTo: .title2, weight: .semibold)
    static let terracottaHeadlineSmall = Font.appSerif(size: 18, relativeTo: .headline, weight: .semibold)

    // Terracotta typography — body/labels/UI
    static let terracottaBodyLarge = Font.appSans(size: 16, relativeTo: .body)
    static let terracottaBodyMedium = Font.appSans(size: 14, relativeTo: .subheadline)
    static let terracottaBodySmall = Font.appSans(size: 12, relativeTo: .caption)
    static let terracottaLabelSmall = Font.appSans(size: 9, relativeTo: .caption2, weight: .bold)
    static let terracottaCategoryPill = Font.appSans(size: 10, relativeTo: .caption2, weight: .semibold)

    static let readerTitle = Font.appSerif(size: 34, relativeTo: .largeTitle, weight: .semibold)
    static let readerControlLabel = Font.appSans(size: 16, relativeTo: .callout, weight: .semibold)

    // Reader typography — regular body copy.
    static let readerBody = Font.appSans(size: ReaderContentStyle.bodyFontSize, relativeTo: .body)
        .weight(ReaderContentStyle.bodyFontWeight)
    static let readerSummaryBody = Font.appSans(
        size: ReaderContentStyle.summaryBodyFontSize,
        relativeTo: .subheadline
    )
        .weight(ReaderContentStyle.bodyFontWeight)
}

extension UIFont {
    static var chatBody: UIFont { appSans(size: 13) }

    static func appSerif(size: CGFloat, weight: UIFont.Weight = .regular) -> UIFont {
        let baseFont = UIFont(name: AppFontFamily.serif, size: size)
            ?? UIFont.systemFont(ofSize: size, weight: weight)
        let descriptor = baseFont.fontDescriptor.addingAttributes([
            .traits: [UIFontDescriptor.TraitKey.weight: weight.rawValue]
        ])
        return UIFont(descriptor: descriptor, size: size)
    }

    static func appSans(size: CGFloat, weight: UIFont.Weight = .regular) -> UIFont {
        let baseFont = UIFont(name: AppFontFamily.sans, size: size)
            ?? UIFont.systemFont(ofSize: size, weight: weight)
        let descriptor = baseFont.fontDescriptor.addingAttributes([
            .traits: [UIFontDescriptor.TraitKey.weight: weight.rawValue]
        ])
        return UIFont(descriptor: descriptor, size: size)
    }

    static func appSans(
        textStyle: UIFont.TextStyle,
        weight: UIFont.Weight = .regular,
        compatibleWith traitCollection: UITraitCollection? = nil
    ) -> UIFont {
        let baseFont = appSans(size: basePointSize(for: textStyle), weight: weight)
        return UIFontMetrics(forTextStyle: textStyle).scaledFont(
            for: baseFont,
            compatibleWith: traitCollection
        )
    }

    static var appReaderBody: UIFont {
        UIFont.appSans(
            size: ReaderContentStyle.bodyFontSize,
            weight: ReaderContentStyle.uiBodyFontWeight
        )
    }

    private static func basePointSize(for textStyle: UIFont.TextStyle) -> CGFloat {
        switch textStyle {
        case .largeTitle: return 34
        case .title1: return 28
        case .title2: return 22
        case .title3: return 20
        case .headline, .body: return 17
        case .callout: return 16
        case .subheadline: return 15
        case .footnote: return 13
        case .caption1: return 12
        case .caption2: return 11
        default: return 17
        }
    }
}

enum ReaderContentStyle {
    static let bodyFontWeight: Font.Weight = .regular
    static let uiBodyFontWeight: UIFont.Weight = .regular
    static let bodyFontSize: CGFloat = 15
    static let summaryBodyFontSize: CGFloat = 14
}

// MARK: - Corner Radius

enum CornerRadius {
    /// Cards and other top-level surfaces.
    static let card: CGFloat = 24
    /// Standalone controls (buttons, toasts) sitting directly on a screen surface.
    static let control: CGFloat = 14
    /// Controls nested inside a card: card radius minus card content padding.
    static let nestedControl: CGFloat = 8
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
    /// Single horizontal content margin used for screen-level gutters.
    static let appHorizontalMargin: CGFloat = 20
    /// Backward-compatible screen gutter alias. Prefer `appHorizontalMargin` in new code.
    static let screenHorizontal: CGFloat = appHorizontalMargin
    /// Backward-compatible Fast Read gutter alias. Prefer `appHorizontalMargin` in new code.
    static let fastReadHorizontal: CGFloat = appHorizontalMargin
    /// Backward-compatible reader gutter alias. Prefer `appHorizontalMargin` in new code.
    static let readerHorizontal: CGFloat = appHorizontalMargin
    /// Backward-compatible chat gutter alias. Prefer `appHorizontalMargin` in new code.
    static let chatHorizontal: CGFloat = appHorizontalMargin
    static let rowHorizontal: CGFloat = 12
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

// MARK: - Motion

enum AppMotion {
    static let press = Animation.spring(response: 0.28, dampingFraction: 0.82)
    static let panel = Animation.spring(duration: 0.3, bounce: 0)
    static let subtle = Animation.easeOut(duration: 0.2)
    static let emphasized = Animation.spring(response: 0.42, dampingFraction: 0.86)
    static let reduced = Animation.linear(duration: 0.01)

    static let recordingPulse = Animation.easeInOut(duration: 1.2).repeatForever(autoreverses: true)
    static let finalizingPulse = Animation.easeInOut(duration: 1.3).repeatForever(autoreverses: true)
    static let voiceLevelPulse = Animation.easeInOut(duration: 0.8).repeatForever(autoreverses: true)
    static let typingDotPulse = Animation.easeInOut(duration: 0.4).repeatForever(autoreverses: true)
    static let loadingBubblePulse = Animation.easeInOut(duration: 0.5).repeatForever(autoreverses: true)
    static let chatStatusPulse = Animation.easeInOut(duration: 1.5).repeatForever(autoreverses: true)
    static let chatIllustrationPulse = Animation.easeInOut(duration: 2.0).repeatForever(autoreverses: true)
    static let landingFloat = Animation.easeInOut(duration: 6).repeatForever(autoreverses: true)
    static let laneShimmer = Animation.linear(duration: 1.6).repeatForever(autoreverses: false)
    static let lanePulse = Animation.easeOut(duration: 1.4).repeatForever(autoreverses: false)

    static func respectingReduceMotion(_ reduceMotion: Bool, _ animation: Animation) -> Animation {
        reduceMotion ? reduced : animation
    }
}

// MARK: - Shadows

struct ShadowLayer {
    let color: Color
    let radius: CGFloat
    let x: CGFloat
    let y: CGFloat
}

struct ShadowStyle {
    let primary: ShadowLayer
    let secondary: ShadowLayer?

    init(
        color: Color,
        radius: CGFloat,
        x: CGFloat = 0,
        y: CGFloat = 0,
        secondary: ShadowLayer? = nil
    ) {
        primary = ShadowLayer(color: color, radius: radius, x: x, y: y)
        self.secondary = secondary
    }

    init(primary: ShadowLayer, secondary: ShadowLayer? = nil) {
        self.primary = primary
        self.secondary = secondary
    }

    static let subtle = ShadowStyle(color: .black.opacity(0.04), radius: 8, x: 0, y: 2)
    static let card = ShadowStyle(color: .black.opacity(0.05), radius: 16, x: 0, y: 10)
    static let elevated = ShadowStyle(color: .black.opacity(0.10), radius: 18, x: 0, y: 12)
    static let floating = ShadowStyle(color: .black.opacity(0.15), radius: 8, x: 0, y: 4)
    static let none = ShadowStyle(color: .clear, radius: 0)
    static let overlayText = ShadowStyle(color: .black.opacity(0.4), radius: 3, x: 0, y: 1)
    static let strongOverlayText = ShadowStyle(color: .black.opacity(0.5), radius: 4, x: 0, y: 1)

    static let editorialCard = ShadowStyle(
        primary: ShadowLayer(color: .black.opacity(0.04), radius: 2, x: 0, y: 1),
        secondary: ShadowLayer(color: .black.opacity(0.06), radius: 24, x: 0, y: 8)
    )

    static let onboardingMic = ShadowStyle(
        primary: ShadowLayer(color: Color.onboardingText.opacity(0.14), radius: 12, x: 10, y: 10),
        // Specular counter-light: visible in light mode, faint on dark charcoal.
        secondary: ShadowLayer(
            color: Color(UIColor { tc in
                UIColor.white.withAlphaComponent(tc.userInterfaceStyle == .dark ? 0.06 : 0.35)
            }),
            radius: 16, x: -8, y: -8
        )
    )

    static func titleGlow(_ color: Color) -> ShadowStyle {
        ShadowStyle(
            primary: ShadowLayer(color: color.opacity(0.6), radius: 16, x: 0, y: 0),
            secondary: ShadowLayer(color: color.opacity(0.3), radius: 32, x: 0, y: 0)
        )
    }

    static func voiceControl(tint: Color, isActive: Bool) -> ShadowStyle {
        ShadowStyle(
            color: tint.opacity(isActive ? 0.22 : 0.12),
            radius: isActive ? 12 : 8,
            y: 6
        )
    }
}

// MARK: - Row Family

enum AppRowFamily {
    case compact
    case regular
}

// MARK: - Kicker Labels

extension Text {
    /// Shared uppercase micro-label style: masthead dates, day delimiters, and metadata rows.
    func kicker(color: Color = .onSurfaceSecondary) -> some View {
        font(.terracottaCategoryPill)
            .tracking(1.5)
            .foregroundStyle(color)
    }
}

// MARK: - View Modifiers

extension View {
    @ViewBuilder
    func accessibilityIdentifier(ifPresent identifier: String?) -> some View {
        if let identifier {
            accessibilityIdentifier(identifier)
        } else {
            self
        }
    }

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

    /// Apply a named shadow preset from the app design tokens.
    func appShadow(_ style: ShadowStyle) -> some View {
        modifier(AppShadowModifier(style: style))
    }

    /// Apply standard screen-level background.
    func screenContainer() -> some View {
        self.background(Color.surfacePrimary)
    }

    /// Fade scrolled content out under the status bar instead of letting it collide
    /// with the clock and Dynamic Island. Solid over the status bar, then a short fade.
    /// Screens with full-bleed artwork under the status bar drive `opacity` from scroll
    /// position so the fade only arrives once that artwork is gone.
    func topScreenEdgeFade(fadeHeight: CGFloat = 14, opacity: Double = 1) -> some View {
        overlay(alignment: .top) {
            TopScreenEdgeFade(fadeHeight: fadeHeight)
                .opacity(opacity)
                .ignoresSafeArea(edges: .top)
                .allowsHitTesting(false)
        }
    }

    /// Keeps text and artwork legible as they approach floating bottom chrome.
    func bottomScreenEdgeFade(fadeHeight: CGFloat = 28) -> some View {
        overlay(alignment: .bottom) {
            LinearGradient(
                colors: [Color.surfacePrimary.opacity(0), Color.surfacePrimary],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: fadeHeight)
            .allowsHitTesting(false)
        }
    }

    /// Uses the app's serif title treatment even when SwiftUI's navigation-bar
    /// appearance proxy has not yet been applied to a newly-created stack.
    func appNavigationTitle(
        _ title: String,
        accessibilityIdentifier: String? = nil
    ) -> some View {
        navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text(title)
                        .accessibilityIdentifier(ifPresent: accessibilityIdentifier)
                        .font(.appHeadline)
                        .foregroundStyle(Color.onSurface)
                }
            }
    }
}

private struct AppShadowModifier: ViewModifier {
    let style: ShadowStyle

    @ViewBuilder
    func body(content: Content) -> some View {
        if let secondary = style.secondary {
            content
                .shadow(
                    color: style.primary.color,
                    radius: style.primary.radius,
                    x: style.primary.x,
                    y: style.primary.y
                )
                .shadow(
                    color: secondary.color,
                    radius: secondary.radius,
                    x: secondary.x,
                    y: secondary.y
                )
        } else {
            content.shadow(
                color: style.primary.color,
                radius: style.primary.radius,
                x: style.primary.x,
                y: style.primary.y
            )
        }
    }
}

private struct TopScreenEdgeFade: View {
    let fadeHeight: CGFloat

    // Overlays on ScrollView don't receive safe-area geometry, so read the
    // window inset directly to size the solid block over the status bar.
    private var topInset: CGFloat {
        UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }
            .first?.safeAreaInsets.top ?? 0
    }

    var body: some View {
        VStack(spacing: 0) {
            Color.surfacePrimary
                .frame(height: topInset)

            LinearGradient(
                colors: [Color.surfacePrimary, Color.surfacePrimary.opacity(0)],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: fadeHeight)
        }
        .frame(maxWidth: .infinity)
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
            if traitCollection.userInterfaceStyle == .dark {
                return UIColor(red: 0.914, green: 0.922, blue: 0.937, alpha: 1.0)  // #e9ebef
            }
            return appOnSurface.resolvedColor(with: traitCollection)
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
