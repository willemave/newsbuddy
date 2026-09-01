//
//  ReaderPalette.swift
//  newsly
//
//  The single, opinionated reader palette shared by SwiftUI tokens and UIKit chrome.
//  Warm cream neutrals with one slate accent, drawn from the ensō app icon. The former
//  multi-palette switcher was removed — there is intentionally no user-facing selection.
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
    /// Long-form body text. Matches onSurface by day; lifted slightly at night so body
    /// copy reads a touch brighter than chrome. Lives here so it warms with the ramp —
    /// it used to be hardcoded in DesignTokens and stayed cool when the palette changed.
    let readerBodyText: AdaptivePaletteColor
    let chatUserBubble: AdaptivePaletteColor
    let outlineVariant: AdaptivePaletteColor
    let borderSubtle: AdaptivePaletteColor
    let borderStrong: AdaptivePaletteColor
}

/// The app's one fixed palette. All color tokens (see DesignTokens) read from here.
enum ReaderPalette {
    static let colors = ReaderPaletteColors(
        // Warm cream neutrals. The previous ramp read as grey in daylight: it was light
        // enough, but near-neutral, so it never looked like paper. Warmth carries the
        // character here rather than lightness. Dark is a matching brown-black.
        surfacePrimary: adaptive(light: 0xfaf6ea, dark: 0x191510),
        surfaceSecondary: adaptive(light: 0xfffdf6, dark: 0x231e17),
        surfaceTertiary: adaptive(light: 0xf2ecdc, dark: 0x282219),
        surfaceContainer: adaptive(light: 0xe6dfc9, dark: 0x332c1f),
        surfaceContainerHigh: adaptive(light: 0xd8d0b7, dark: 0x3e3626),
        surfaceContainerHighest: adaptive(light: 0xc8bfa3, dark: 0x4b422f),
        // Slate accent, taken from the app icon's brush ring. Dark lifts the same hue
        // rather than substituting another — #3f4c60 is invisible on a dark ground, and
        // it lifts a little further here to clear the warmer ground.
        brandPrimary: adaptive(light: 0x3f4c60, dark: 0x9db0cc),
        brandPrimaryStrong: adaptive(light: 0x333e50, dark: 0xb3c3da),
        // Secondary/tertiary are warm neutrals, not second/third hues (single-accent doctrine).
        brandSecondary: adaptive(light: 0x716c5c, dark: 0x9d9581),
        brandTertiary: adaptive(light: 0x757060, dark: 0x8f8876),
        onSurface: adaptive(light: 0x24221a, dark: 0xefe9da),
        onSurfaceSecondary: adaptive(light: 0x716c5c, dark: 0x9d9581),
        onSurfaceTertiary: adaptive(light: 0x757060, dark: 0x8f8876),
        readerBodyText: adaptive(light: 0x24221a, dark: 0xf3eee1),
        chatUserBubble: adaptive(light: 0xf4ecd8, dark: 0x332b1c),
        outlineVariant: adaptive(light: 0xe8e1cf, dark: 0x352f24),
        borderSubtle: adaptive(light: 0xefe8d6, dark: 0x282219),
        borderStrong: adaptive(light: 0xb9b096, dark: 0x665c45)
    )

    /// Trait-aware UIColor for a palette slot. Resolves light/dark at draw time.
    static func selectedUIColor(_ keyPath: KeyPath<ReaderPaletteColors, AdaptivePaletteColor>) -> UIColor {
        UIColor { traitCollection in
            colors[keyPath: keyPath].uiColor(for: traitCollection)
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
