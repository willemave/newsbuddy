//
//  ReaderPalette.swift
//  newsly
//
//  The single, opinionated reader palette shared by SwiftUI tokens and UIKit chrome.
//  Cool "slate" neutrals with one amber accent. The former multi-palette switcher
//  was removed — there is intentionally no user-facing color selection.
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

/// The app's one fixed palette. All color tokens (see DesignTokens) read from here.
enum ReaderPalette {
    static let colors = ReaderPaletteColors(
        // Warm paper neutrals. Dark surfaces sit on a lifted warm charcoal rather than
        // near-black, with wide steps between rungs so unselected chips stay visible.
        surfacePrimary: adaptive(light: 0xf8f6f1, dark: 0x171613),
        surfaceSecondary: adaptive(light: 0xfffcf7, dark: 0x1f1e1a),
        surfaceTertiary: adaptive(light: 0xedeae2, dark: 0x262521),
        surfaceContainer: adaptive(light: 0xe3dfd5, dark: 0x2e2c27),
        surfaceContainerHigh: adaptive(light: 0xd6d1c5, dark: 0x393731),
        surfaceContainerHighest: adaptive(light: 0xc6c0b2, dark: 0x46433c),
        // Slate accent, taken from the app icon's brush ring. Dark lifts the same hue
        // rather than substituting another — #3f4c60 is invisible on a dark ground.
        brandPrimary: adaptive(light: 0x3f4c60, dark: 0x93a7c4),
        brandPrimaryStrong: adaptive(light: 0x333e50, dark: 0xaabdd8),
        // Secondary/tertiary are warm neutrals, not second/third hues (single-accent doctrine).
        brandSecondary: adaptive(light: 0x6e6a61, dark: 0x9a958a),
        brandTertiary: adaptive(light: 0x757067, dark: 0x8d887e),
        onSurface: adaptive(light: 0x22211d, dark: 0xece8e0),
        onSurfaceSecondary: adaptive(light: 0x6e6a61, dark: 0x9a958a),
        onSurfaceTertiary: adaptive(light: 0x757067, dark: 0x8d887e),
        chatUserBubble: adaptive(light: 0xefe7d8, dark: 0x2b2620),
        outlineVariant: adaptive(light: 0xdcd7cb, dark: 0x413e37),
        borderSubtle: adaptive(light: 0xe7e3da, dark: 0x2e2c27),
        borderStrong: adaptive(light: 0xb5afa1, dark: 0x635e54)
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
