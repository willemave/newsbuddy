//
//  ShareExtensionStyle.swift
//  newsly
//

import UIKit

enum ShareExtensionStyle {
    static let brandColorAssetName = "ShareBrandPrimary"
    static let bodyFamily = "Lato-Regular"
    static let titleFamily = "Lora-Regular"

    static var brandAccent: UIColor {
        UIColor(named: brandColorAssetName) ?? UIColor { traitCollection in
            traitCollection.userInterfaceStyle == .dark
                ? UIColor(red: 0.878, green: 0.529, blue: 0.373, alpha: 1.0)
                : UIColor(red: 0.757, green: 0.373, blue: 0.235, alpha: 1.0)
        }
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
        let preferred = UIFont.preferredFont(forTextStyle: textStyle)
        let baseFont = UIFont(name: family, size: preferred.pointSize)
            ?? UIFont.systemFont(ofSize: preferred.pointSize, weight: weight)
        let descriptor = baseFont.fontDescriptor.addingAttributes([
            .traits: [UIFontDescriptor.TraitKey.weight: weight.rawValue]
        ])
        let weightedFont = UIFont(descriptor: descriptor, size: preferred.pointSize)
        return UIFontMetrics(forTextStyle: textStyle).scaledFont(for: weightedFont)
    }
}
