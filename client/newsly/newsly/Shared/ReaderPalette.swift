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
        // Dark surfaces sit on a lifted charcoal rather than near-black, with wider
        // steps between rungs so unselected chips stay visible against the ground.
        surfacePrimary: adaptive(light: 0xf4f5f7, dark: 0x131519),
        surfaceSecondary: adaptive(light: 0xffffff, dark: 0x1b1e23),
        surfaceTertiary: adaptive(light: 0xe8eaee, dark: 0x21252b),
        surfaceContainer: adaptive(light: 0xdcdee4, dark: 0x282c33),
        surfaceContainerHigh: adaptive(light: 0xcdd1d9, dark: 0x333841),
        surfaceContainerHighest: adaptive(light: 0xbcc1cb, dark: 0x40454f),
        // Amber accent — the single opinionated brand hue.
        brandPrimary: adaptive(light: 0x99610a, dark: 0xe0a33f),
        brandPrimaryStrong: adaptive(light: 0x8f5a08, dark: 0xc98a2c),
        // Secondary/tertiary are cool neutrals, not second/third hues (single-accent doctrine).
        brandSecondary: adaptive(light: 0x676c76, dark: 0x8f959f),
        brandTertiary: adaptive(light: 0x6a707b, dark: 0x818792),
        onSurface: adaptive(light: 0x1b1e24, dark: 0xe5e7ec),
        onSurfaceSecondary: adaptive(light: 0x676c76, dark: 0x8f959f),
        onSurfaceTertiary: adaptive(light: 0x6a707b, dark: 0x818792),
        chatUserBubble: adaptive(light: 0xefe7d8, dark: 0x2a2417),
        outlineVariant: adaptive(light: 0xcdd1d9, dark: 0x3b404a),
        borderSubtle: adaptive(light: 0xdcdee4, dark: 0x282c33),
        borderStrong: adaptive(light: 0xaab0bb, dark: 0x5b626d)
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
