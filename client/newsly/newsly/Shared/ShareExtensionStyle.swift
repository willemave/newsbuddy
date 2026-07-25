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
