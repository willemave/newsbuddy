//
//  ReaderPalette.swift
//  newsly
//
//  The single, opinionated reader palette shared by SwiftUI tokens and UIKit chrome.
//  Warm "newspaper" neutrals with one terracotta accent. The former multi-palette
//  switcher was removed — there is intentionally no user-facing color selection.
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
        surfacePrimary: adaptive(light: 0xf5f2ea, dark: 0x10100e),
        surfaceSecondary: adaptive(light: 0xfffdf8, dark: 0x1e1d1b),
        surfaceTertiary: adaptive(light: 0xe7e2d4, dark: 0x232220),
        surfaceContainer: adaptive(light: 0xddd5c2, dark: 0x2c2a27),
        surfaceContainerHigh: adaptive(light: 0xd1c7b2, dark: 0x353430),
        surfaceContainerHighest: adaptive(light: 0xc4b899, dark: 0x413e39),
        // Terracotta accent — the single opinionated brand hue.
        brandPrimary: adaptive(light: 0xc15f3c, dark: 0xe0875f),
        brandPrimaryStrong: adaptive(light: 0xa34d2e, dark: 0xc97550),
        // Secondary/tertiary are warm neutrals, not second/third hues (single-accent doctrine).
        brandSecondary: adaptive(light: 0x6d6a61, dark: 0xa8a397),
        brandTertiary: adaptive(light: 0x8d8778, dark: 0x837d6e),
        onSurface: adaptive(light: 0x24231f, dark: 0xece8dc),
        onSurfaceSecondary: adaptive(light: 0x6d6a61, dark: 0xa8a397),
        onSurfaceTertiary: adaptive(light: 0x8d8778, dark: 0x837d6e),
        chatUserBubble: adaptive(light: 0xeadfdd, dark: 0x301f1f),
        outlineVariant: adaptive(light: 0xc9c1ae, dark: 0x4c483d),
        borderSubtle: adaptive(light: 0xd4cdbb, dark: 0x38352d),
        borderStrong: adaptive(light: 0xa69771, dark: 0x76715e)
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
