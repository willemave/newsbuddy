import UIKit

/// Two-character monogram drawn in a small rounded square before a news
/// source link so readers can tell which aggregator surfaced it.
enum BriefingAggregatorBadge {
    private static let knownMonograms: [String: String] = [
        "hackernews": "HN",
        "techmeme": "Tm",
        "memeorandum": "Mm",
        "mediagazer": "Mg",
        "twitter": "X",
        "reddit": "r/"
    ]

    static func monogram(for aggregator: String) -> String? {
        let key = aggregator.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if let known = knownMonograms[key] {
            return known
        }
        let letters = key.filter(\.isLetter)
        guard let first = letters.first else { return nil }
        let second = letters.dropFirst().first.map(String.init) ?? ""
        return first.uppercased() + second
    }

    /// Faint fill plus solid letters in one template image, so a single
    /// dynamic tint keeps both layers correct in light and dark mode. Sized
    /// from the Dynamic Type-scaled body font because attachments don't scale.
    static func attachment(monogram: String, alignedTo font: UIFont) -> NSTextAttachment {
        let scaledFont = UIFontMetrics(forTextStyle: .callout).scaledFont(for: font)
        let height = (scaledFont.capHeight + 7).rounded()
        let text = NSAttributedString(string: monogram, attributes: [
            .font: UIFont.appSans(size: (height * 0.55).rounded(), weight: .bold),
            .foregroundColor: UIColor.black
        ])
        let textSize = text.size()
        let size = CGSize(width: max(height, (textSize.width + 6).rounded()), height: height)
        let image = UIGraphicsImageRenderer(size: size).image { _ in
            UIColor.black.withAlphaComponent(0.16).setFill()
            UIBezierPath(roundedRect: CGRect(origin: .zero, size: size), cornerRadius: height * 0.24).fill()
            text.draw(at: CGPoint(
                x: (size.width - textSize.width) / 2,
                y: (size.height - textSize.height) / 2
            ))
        }
        .withTintColor(.appOnSurfaceSecondary, renderingMode: .alwaysOriginal)

        let attachment = NSTextAttachment(image: image)
        attachment.bounds = CGRect(
            x: 0,
            y: ((scaledFont.capHeight - height) / 2).rounded(),
            width: size.width,
            height: size.height
        )
        return attachment
    }
}
