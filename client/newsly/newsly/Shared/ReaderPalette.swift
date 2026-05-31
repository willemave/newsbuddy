//
//  ReaderPalette.swift
//  newsly
//
//  App-wide reader palette definitions shared by SwiftUI tokens and UIKit chrome.
//

import SwiftUI
import UIKit

struct AdaptivePaletteColor {
    let light: UIColor
    let dark: UIColor

    func uiColor(for traitCollection: UITraitCollection) -> UIColor {
        traitCollection.userInterfaceStyle == .dark ? dark : light
    }
}

struct ReaderPaletteColors {
    let surfacePrimary: AdaptivePaletteColor
    let surfaceSecondary: AdaptivePaletteColor
    let surfaceTertiary: AdaptivePaletteColor
    let surfaceContainer: AdaptivePaletteColor
    let surfaceContainerHigh: AdaptivePaletteColor
    let surfaceContainerHighest: AdaptivePaletteColor
    let brandPrimary: AdaptivePaletteColor
    let brandPrimaryStrong: AdaptivePaletteColor
    let brandSecondary: AdaptivePaletteColor
    let brandTertiary: AdaptivePaletteColor
    let onSurface: AdaptivePaletteColor
    let onSurfaceSecondary: AdaptivePaletteColor
    let onSurfaceTertiary: AdaptivePaletteColor
    let chatUserBubble: AdaptivePaletteColor
    let outlineVariant: AdaptivePaletteColor
    let borderSubtle: AdaptivePaletteColor
    let borderStrong: AdaptivePaletteColor
}

enum ReaderPalette: String, CaseIterable, Identifiable {
    case graphiteSlate
    case inkEucalyptus
    case charcoalDustyRose
    case aubergineMist
    case newspaperOxblood
    case porcelainGreen

    static let storageKey = "readerPaletteId"
    static let defaultPalette: ReaderPalette = .newspaperOxblood

    var id: String { rawValue }

    static var selected: ReaderPalette {
        guard let stored = SharedContainer.userDefaults.string(forKey: storageKey) else {
            return defaultPalette
        }
        return ReaderPalette(rawValue: stored) ?? defaultPalette
    }

    var displayName: String {
        switch self {
        case .graphiteSlate: return "Graphite + Slate"
        case .inkEucalyptus: return "Ink + Eucalyptus"
        case .charcoalDustyRose: return "Charcoal + Dusty Rose"
        case .aubergineMist: return "Aubergine + Mist"
        case .newspaperOxblood: return "Newspaper + Oxblood"
        case .porcelainGreen: return "Soft Black + Porcelain"
        }
    }

    var summary: String {
        switch self {
        case .graphiteSlate: return "Cool slate blue, lilac grey, and sage."
        case .inkEucalyptus: return "Soft green with dusty blue and clay rose."
        case .charcoalDustyRose: return "Editorial rose with mineral blue."
        case .aubergineMist: return "Deep violet base with mist blue."
        case .newspaperOxblood: return "Classic reader tones with a restrained red accent."
        case .porcelainGreen: return "Quiet green, faded denim, and muted plum."
        }
    }

    var colors: ReaderPaletteColors {
        switch self {
        case .graphiteSlate:
            return ReaderPaletteColors(
                surfacePrimary: Self.adaptive(light: 0xf4f6f7, dark: 0x111213),
                surfaceSecondary: Self.adaptive(light: 0xffffff, dark: 0x1b1c1e),
                surfaceTertiary: Self.adaptive(light: 0xe8edf1, dark: 0x25272a),
                surfaceContainer: Self.adaptive(light: 0xdce4ea, dark: 0x2d3034),
                surfaceContainerHigh: Self.adaptive(light: 0xccd7df, dark: 0x373b3f),
                surfaceContainerHighest: Self.adaptive(light: 0xb9c7d2, dark: 0x43474c),
                brandPrimary: Self.adaptive(light: 0x587285, dark: 0x8fa8bc),
                brandPrimaryStrong: Self.adaptive(light: 0x405b70, dark: 0x6f8ca3),
                brandSecondary: Self.adaptive(light: 0x56616a, dark: 0xa9b2b8),
                brandTertiary: Self.adaptive(light: 0x74808a, dark: 0x748089),
                onSurface: Self.adaptive(light: 0x151a1e, dark: 0xeef3f4),
                onSurfaceSecondary: Self.adaptive(light: 0x56616a, dark: 0xa9b2b8),
                onSurfaceTertiary: Self.adaptive(light: 0x74808a, dark: 0x748089),
                chatUserBubble: Self.adaptive(light: 0xdce7ee, dark: 0x1d2831),
                outlineVariant: Self.adaptive(light: 0xc4cdd5, dark: 0x33414c),
                borderSubtle: Self.adaptive(light: 0xd3dbe2, dark: 0x28333b),
                borderStrong: Self.adaptive(light: 0x8998a6, dark: 0x5f7080)
            )
        case .inkEucalyptus:
            return ReaderPaletteColors(
                surfacePrimary: Self.adaptive(light: 0xf3f7f4, dark: 0x101211),
                surfaceSecondary: Self.adaptive(light: 0xffffff, dark: 0x1b1e1c),
                surfaceTertiary: Self.adaptive(light: 0xe6eee9, dark: 0x242926),
                surfaceContainer: Self.adaptive(light: 0xd7e4dc, dark: 0x2d322e),
                surfaceContainerHigh: Self.adaptive(light: 0xc8d8ce, dark: 0x373e3a),
                surfaceContainerHighest: Self.adaptive(light: 0xb6cabd, dark: 0x444c48),
                brandPrimary: Self.adaptive(light: 0x587865, dark: 0x8fb8a2),
                brandPrimaryStrong: Self.adaptive(light: 0x3f5f4d, dark: 0x6e9a83),
                brandSecondary: Self.adaptive(light: 0x526059, dark: 0xaab8af),
                brandTertiary: Self.adaptive(light: 0x708077, dark: 0x74857b),
                onSurface: Self.adaptive(light: 0x101815, dark: 0xedf4ef),
                onSurfaceSecondary: Self.adaptive(light: 0x526059, dark: 0xaab8af),
                onSurfaceTertiary: Self.adaptive(light: 0x708077, dark: 0x74857b),
                chatUserBubble: Self.adaptive(light: 0xdbe9e0, dark: 0x1b2a22),
                outlineVariant: Self.adaptive(light: 0xc0cdc4, dark: 0x33463a),
                borderSubtle: Self.adaptive(light: 0xd2ded6, dark: 0x29362e),
                borderStrong: Self.adaptive(light: 0x899b90, dark: 0x62796a)
            )
        case .charcoalDustyRose:
            return ReaderPaletteColors(
                surfacePrimary: Self.adaptive(light: 0xf8f4f5, dark: 0x111011),
                surfaceSecondary: Self.adaptive(light: 0xffffff, dark: 0x1c191a),
                surfaceTertiary: Self.adaptive(light: 0xefe6e8, dark: 0x252122),
                surfaceContainer: Self.adaptive(light: 0xe3d5d9, dark: 0x2f2a2c),
                surfaceContainerHigh: Self.adaptive(light: 0xd7c5cb, dark: 0x3b3437),
                surfaceContainerHighest: Self.adaptive(light: 0xc9b3bb, dark: 0x473f42),
                brandPrimary: Self.adaptive(light: 0x8a5c64, dark: 0xc79aa1),
                brandPrimaryStrong: Self.adaptive(light: 0x633e46, dark: 0xa97981),
                brandSecondary: Self.adaptive(light: 0x63575b, dark: 0xb8aaae),
                brandTertiary: Self.adaptive(light: 0x827478, dark: 0x887a7e),
                onSurface: Self.adaptive(light: 0x1b1416, dark: 0xf3ecee),
                onSurfaceSecondary: Self.adaptive(light: 0x63575b, dark: 0xb8aaae),
                onSurfaceTertiary: Self.adaptive(light: 0x827478, dark: 0x887a7e),
                chatUserBubble: Self.adaptive(light: 0xeadce0, dark: 0x2d1f24),
                outlineVariant: Self.adaptive(light: 0xd1c0c6, dark: 0x4a3940),
                borderSubtle: Self.adaptive(light: 0xdfd2d6, dark: 0x382a2f),
                borderStrong: Self.adaptive(light: 0xa28c94, dark: 0x785f68)
            )
        case .aubergineMist:
            return ReaderPaletteColors(
                surfacePrimary: Self.adaptive(light: 0xf5f4f8, dark: 0x121114),
                surfaceSecondary: Self.adaptive(light: 0xffffff, dark: 0x1e1d21),
                surfaceTertiary: Self.adaptive(light: 0xe9e6f0, dark: 0x29272f),
                surfaceContainer: Self.adaptive(light: 0xddd8e8, dark: 0x33313b),
                surfaceContainerHigh: Self.adaptive(light: 0xd0c9dc, dark: 0x3d3b47),
                surfaceContainerHighest: Self.adaptive(light: 0xc1b8cf, dark: 0x494654),
                brandPrimary: Self.adaptive(light: 0x576f86, dark: 0x91abc3),
                brandPrimaryStrong: Self.adaptive(light: 0x3e5369, dark: 0x6f8faa),
                brandSecondary: Self.adaptive(light: 0x5b5867, dark: 0xb2aeba),
                brandTertiary: Self.adaptive(light: 0x777284, dark: 0x807a8d),
                onSurface: Self.adaptive(light: 0x17151e, dark: 0xf1eff6),
                onSurfaceSecondary: Self.adaptive(light: 0x5b5867, dark: 0xb2aeba),
                onSurfaceTertiary: Self.adaptive(light: 0x777284, dark: 0x807a8d),
                chatUserBubble: Self.adaptive(light: 0xe2dfec, dark: 0x242033),
                outlineVariant: Self.adaptive(light: 0xc8c1d2, dark: 0x423c58),
                borderSubtle: Self.adaptive(light: 0xd9d4e3, dark: 0x302c41),
                borderStrong: Self.adaptive(light: 0x948aa4, dark: 0x6c6385)
            )
        case .newspaperOxblood:
            return ReaderPaletteColors(
                surfacePrimary: Self.adaptive(light: 0xf5f2ea, dark: 0x10100e),
                surfaceSecondary: Self.adaptive(light: 0xfffdf8, dark: 0x191817),
                surfaceTertiary: Self.adaptive(light: 0xe7e2d4, dark: 0x232220),
                surfaceContainer: Self.adaptive(light: 0xddd5c2, dark: 0x2c2a27),
                surfaceContainerHigh: Self.adaptive(light: 0xd1c7b2, dark: 0x353430),
                surfaceContainerHighest: Self.adaptive(light: 0xc4b899, dark: 0x413e39),
                brandPrimary: Self.adaptive(light: 0x864f4f, dark: 0xb87979),
                brandPrimaryStrong: Self.adaptive(light: 0x643939, dark: 0x965c5c),
                brandSecondary: Self.adaptive(light: 0x6d6a61, dark: 0xa8a397),
                brandTertiary: Self.adaptive(light: 0x8d8778, dark: 0x837d6e),
                onSurface: Self.adaptive(light: 0x24231f, dark: 0xece8dc),
                onSurfaceSecondary: Self.adaptive(light: 0x6d6a61, dark: 0xa8a397),
                onSurfaceTertiary: Self.adaptive(light: 0x8d8778, dark: 0x837d6e),
                chatUserBubble: Self.adaptive(light: 0xeadfdd, dark: 0x301f1f),
                outlineVariant: Self.adaptive(light: 0xc9c1ae, dark: 0x4c483d),
                borderSubtle: Self.adaptive(light: 0xd4cdbb, dark: 0x38352d),
                borderStrong: Self.adaptive(light: 0xa69771, dark: 0x76715e)
            )
        case .porcelainGreen:
            return ReaderPaletteColors(
                surfacePrimary: Self.adaptive(light: 0xf3f7f4, dark: 0x101010),
                surfaceSecondary: Self.adaptive(light: 0xffffff, dark: 0x191a19),
                surfaceTertiary: Self.adaptive(light: 0xe5eee9, dark: 0x232623),
                surfaceContainer: Self.adaptive(light: 0xd7e3dd, dark: 0x2d312f),
                surfaceContainerHigh: Self.adaptive(light: 0xc8d7d0, dark: 0x383d3a),
                surfaceContainerHighest: Self.adaptive(light: 0xb9cbbf, dark: 0x434a46),
                brandPrimary: Self.adaptive(light: 0x637e6e, dark: 0xa8c5b4),
                brandPrimaryStrong: Self.adaptive(light: 0x486553, dark: 0x7fa38f),
                brandSecondary: Self.adaptive(light: 0x55615a, dark: 0xaeb9b3),
                brandTertiary: Self.adaptive(light: 0x748078, dark: 0x7b8780),
                onSurface: Self.adaptive(light: 0x111614, dark: 0xeff5f1),
                onSurfaceSecondary: Self.adaptive(light: 0x55615a, dark: 0xaeb9b3),
                onSurfaceTertiary: Self.adaptive(light: 0x748078, dark: 0x7b8780),
                chatUserBubble: Self.adaptive(light: 0xdde9e2, dark: 0x1d2822),
                outlineVariant: Self.adaptive(light: 0xc3cec7, dark: 0x35443b),
                borderSubtle: Self.adaptive(light: 0xd4ded8, dark: 0x2a362f),
                borderStrong: Self.adaptive(light: 0x8b9c92, dark: 0x667a6c)
            )
        }
    }

    var swatches: [Color] {
        [
            color(\.surfacePrimary),
            color(\.surfaceTertiary),
            color(\.brandPrimary),
            color(\.onSurfaceSecondary),
            color(\.onSurface)
        ]
    }

    func color(_ keyPath: KeyPath<ReaderPaletteColors, AdaptivePaletteColor>) -> Color {
        Color(uiColor(keyPath))
    }

    func uiColor(_ keyPath: KeyPath<ReaderPaletteColors, AdaptivePaletteColor>) -> UIColor {
        UIColor { traitCollection in
            colors[keyPath: keyPath].uiColor(for: traitCollection)
        }
    }

    static func selectedUIColor(_ keyPath: KeyPath<ReaderPaletteColors, AdaptivePaletteColor>) -> UIColor {
        UIColor { traitCollection in
            selected.colors[keyPath: keyPath].uiColor(for: traitCollection)
        }
    }

    private static func adaptive(light: UInt32, dark: UInt32) -> AdaptivePaletteColor {
        AdaptivePaletteColor(light: UIColor(hex: light), dark: UIColor(hex: dark))
    }
}

private extension UIColor {
    convenience init(hex: UInt32) {
        self.init(
            red: CGFloat((hex >> 16) & 0xff) / 255.0,
            green: CGFloat((hex >> 8) & 0xff) / 255.0,
            blue: CGFloat(hex & 0xff) / 255.0,
            alpha: 1.0
        )
    }
}
