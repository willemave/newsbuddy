import UIKit

let BriefingInsightAttributeName = NSAttributedString.Key("BriefingInsightID")

struct BriefingAttributedTextBuilder {
    struct Result {
        let attributedText: NSAttributedString
        let plainText: String
    }

    func build(paragraphs: [APIBriefingParagraph], weight: String?) -> Result {
        let output = NSMutableAttributedString()
        let baseFont = font(for: weight, bold: false)
        let paragraphStyle = NSMutableParagraphStyle()
        paragraphStyle.lineSpacing = weight == "feature" ? 4 : 2
        paragraphStyle.paragraphSpacing = 10

        for (paragraphIndex, paragraph) in paragraphs.enumerated() {
            if paragraphIndex > 0 {
                output.append(NSAttributedString(string: "\n\n"))
            }
            for run in paragraph.runs {
                var attributes: [NSAttributedString.Key: Any] = [
                    .font: font(for: weight, bold: run.bold),
                    .foregroundColor: UIColor.appReaderBodyText,
                    .paragraphStyle: paragraphStyle
                ]
                if run.kind == .source_link, let sourceKey = run.sourceKey {
                    attributes[.link] = url(for: sourceKey)
                    attributes[.foregroundColor] = UIColor.appAccent
                    attributes[.underlineStyle] = NSUnderlineStyle.single.rawValue
                }
                if run.kind == .insight, let insightId = run.insightId {
                    attributes[BriefingInsightAttributeName] = insightId
                    attributes[.underlineStyle] = NSUnderlineStyle.single.rawValue
                        | NSUnderlineStyle.patternDot.rawValue
                    attributes[.underlineColor] = UIColor.appAccent.withAlphaComponent(0.75)
                }
                if run.kind == .text && run.bold {
                    attributes[.font] = baseFont.withWeight(.semibold)
                }
                output.append(NSAttributedString(string: run.text, attributes: attributes))
            }
        }

        return Result(attributedText: output, plainText: output.string)
    }

    private func font(for weight: String?, bold: Bool) -> UIFont {
        if weight == "feature" {
            return UIFont.appSerif(size: 19, weight: bold ? .semibold : .regular)
        }
        return UIFont.appSans(size: 16, weight: bold ? .semibold : .regular)
    }

    private func url(for sourceKey: String) -> URL? {
        let parts = sourceKey.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return nil }
        return URL(string: "newsly://briefing/\(parts[0])/\(parts[1])")
    }
}

private extension UIFont {
    func withWeight(_ weight: UIFont.Weight) -> UIFont {
        let descriptor = fontDescriptor.addingAttributes([
            .traits: [UIFontDescriptor.TraitKey.weight: weight.rawValue]
        ])
        return UIFont(descriptor: descriptor, size: pointSize)
    }
}
