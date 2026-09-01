//
//  ShareExtensionStyle.swift
//  newsly
//

import UIKit

enum ShareExtensionStyle {
    static let bodyFamily = "Lato-Regular"
    static let titleFamily = "Lora-Regular"

    static var brandAccent: UIColor {
        ReaderPalette.selectedUIColor(\.brandPrimary)
    }

    // The sheet used stock system colors, which read as an iOS-default panel bolted onto
    // a warm-cream app. These mirror the app's surface roles.
    static var surface: UIColor {
        ReaderPalette.selectedUIColor(\.surfacePrimary)
    }

    static var surfaceElevated: UIColor {
        ReaderPalette.selectedUIColor(\.surfaceSecondary)
    }

    static var surfaceHighlight: UIColor {
        ReaderPalette.selectedUIColor(\.surfaceContainer)
    }

    static var textPrimary: UIColor {
        ReaderPalette.selectedUIColor(\.onSurface)
    }

    static var textSecondary: UIColor {
        ReaderPalette.selectedUIColor(\.onSurfaceSecondary)
    }

    static var hairline: UIColor {
        ReaderPalette.selectedUIColor(\.outlineVariant)
    }

    /// Foreground for content on `brandAccent`. Inverts with the accent — white fails
    /// against the light dark-mode accent.
    static var onAccent: UIColor {
        ReaderPalette.selectedUIColor(\.surfacePrimary)
    }

    static func font(textStyle: UIFont.TextStyle, weight: UIFont.Weight = .regular) -> UIFont {
        scaledFont(named: bodyFamily, textStyle: textStyle, weight: weight)
    }

    static func titleFont(textStyle: UIFont.TextStyle) -> UIFont {
        scaledFont(named: titleFamily, textStyle: textStyle)
    }

    private static func scaledFont(
        named family: String,
        textStyle: UIFont.TextStyle,
        weight: UIFont.Weight = .regular
    ) -> UIFont {
        let pointSize = basePointSize(for: textStyle)
        let baseFont = UIFont(name: family, size: pointSize)
            ?? UIFont.systemFont(ofSize: pointSize, weight: weight)
        let descriptor = baseFont.fontDescriptor.addingAttributes([
            .traits: [UIFontDescriptor.TraitKey.weight: weight.rawValue]
        ])
        let weightedFont = UIFont(descriptor: descriptor, size: pointSize)
        return UIFontMetrics(forTextStyle: textStyle).scaledFont(for: weightedFont)
    }

    private static func basePointSize(for textStyle: UIFont.TextStyle) -> CGFloat {
        switch textStyle {
        case .largeTitle: 34
        case .title1: 28
        case .title2: 22
        case .title3: 20
        case .headline, .body: 17
        case .callout: 16
        case .subheadline: 15
        case .footnote: 13
        case .caption1: 12
        case .caption2: 11
        default: 17
        }
    }
}
