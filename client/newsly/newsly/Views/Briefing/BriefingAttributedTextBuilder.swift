import UIKit

let BriefingInsightAttributeName = NSAttributedString.Key("BriefingInsightID")

/// Inline discussion affordance rendered after a source link: a small
/// bubble icon plus comment count that links out to the discussion sheet.
struct BriefingDiscussionChip: Equatable {
    let sourceKey: String
    let commentCount: Int?
}

struct BriefingAttributedTextBuilder {
    struct Result {
        let attributedText: NSAttributedString
        let plainText: String
    }

    func build(
        paragraphs: [APIBriefingParagraph],
        weight: String?,
        discussionChips: [String: BriefingDiscussionChip] = [:]
    ) -> Result {
        let output = NSMutableAttributedString()
        let baseFont = font(for: weight, bold: false)
        let paragraphStyle = NSMutableParagraphStyle()
        paragraphStyle.lineSpacing = weight == "feature" ? 4 : 2
        paragraphStyle.paragraphSpacing = 10
        var emittedChipSourceKeys = Set<String>()

        for (paragraphIndex, paragraph) in paragraphs.enumerated() {
            if paragraphIndex > 0 {
                output.append(NSAttributedString(string: "\n\n"))
            }
            let runs = paragraph.runs
            for (runIndex, run) in runs.enumerated() {
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
                let text = Self.sanitizedRunText(for: run, at: runIndex, in: runs)
                guard !text.isEmpty else { continue }
                output.append(NSAttributedString(string: text, attributes: attributes))

                if run.kind == .source_link,
                   let sourceKey = run.sourceKey,
                   let chip = discussionChips[sourceKey],
                   emittedChipSourceKeys.insert(sourceKey).inserted {
                    output.append(discussionChipText(for: chip, paragraphStyle: paragraphStyle))
                }
            }
        }

        return Result(attributedText: output, plainText: output.string)
    }

    private func discussionChipText(
        for chip: BriefingDiscussionChip,
        paragraphStyle: NSParagraphStyle
    ) -> NSAttributedString {
        let chipFont = UIFont.appSans(size: 12, weight: .semibold)
        let output = NSMutableAttributedString()
        var attributes: [NSAttributedString.Key: Any] = [
            .font: chipFont,
            .foregroundColor: UIColor.appAccent,
            .paragraphStyle: paragraphStyle
        ]
        if let link = discussionURL(for: chip.sourceKey) {
            attributes[.link] = link
        }

        // Non-breaking space keeps the chip glued to the link it annotates.
        output.append(NSAttributedString(string: "\u{00A0}", attributes: [
            .font: chipFont,
            .paragraphStyle: paragraphStyle
        ]))

        let symbolConfiguration = UIImage.SymbolConfiguration(pointSize: 10, weight: .semibold)
        if let icon = UIImage(systemName: "bubble.left.and.bubble.right.fill", withConfiguration: symbolConfiguration)?
            .withTintColor(.appAccent, renderingMode: .alwaysOriginal) {
            let attachment = NSTextAttachment(image: icon)
            attachment.bounds = CGRect(
                x: 0,
                y: (chipFont.capHeight - icon.size.height) / 2,
                width: icon.size.width,
                height: icon.size.height
            )
            output.append(NSAttributedString(attachment: attachment))
            // Merge link/paragraph attributes onto the attachment character so
            // tapping the icon opens the discussion too.
            output.addAttributes(
                attributes,
                range: NSRange(location: output.length - 1, length: 1)
            )
        }

        if let count = chip.commentCount, count > 0 {
            // Narrow no-break space: the icon and count must wrap as one unit.
            output.append(NSAttributedString(
                string: "\u{202F}\(Self.compactCount(count))",
                attributes: attributes
            ))
        }
        return output
    }

    /// Older stored briefings can carry leftover `**` markers where the
    /// composer bolded a source link (`**[title](url)**`); the parser split at
    /// the link and leaked the markers as literal text. Strip them only when
    /// they touch a source link so genuine asterisks in prose survive.
    static func sanitizedRunText(
        for run: APIBriefingRun,
        at index: Int,
        in runs: [APIBriefingRun]
    ) -> String {
        guard run.kind == .text else { return run.text }
        var text = run.text
        let previousIsLink = index > 0 && runs[index - 1].kind == .source_link
        let nextIsLink = index + 1 < runs.count && runs[index + 1].kind == .source_link
        if previousIsLink, text.hasPrefix("**") {
            text = String(text.dropFirst(2))
        }
        if nextIsLink, text.hasSuffix("**") {
            text = String(text.dropLast(2))
        }
        return text
    }

    static func compactCount(_ count: Int) -> String {
        guard count >= 1000 else { return "\(count)" }
        let thousands = Double(count) / 1000
        let rounded = (thousands * 10).rounded() / 10
        if rounded >= 10 || rounded == rounded.rounded() {
            return "\(Int(rounded.rounded()))k"
        }
        return String(format: "%.1fk", rounded)
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

    private func discussionURL(for sourceKey: String) -> URL? {
        let parts = sourceKey.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return nil }
        return URL(string: "newsly://briefing/discussion/\(parts[0])/\(parts[1])")
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
