//
//  SelectableMarkdownView.swift
//  newsly
//
//  Created by Assistant on 2/14/26.
//

import SwiftUI
import UIKit

/// A markdown-rendered text view that supports word-level text selection
/// with "Dig Deeper" in the edit menu, using `DigDeeperTextView`.
struct SelectableMarkdownView: UIViewRepresentable {
    let markdown: String
    let textColor: UIColor
    let baseFont: UIFont
    let adjustsFontForContentSizeCategory: Bool
    let scalingTextStyle: UIFont.TextStyle
    var onDigDeeper: ((String) -> Void)?

    @Environment(\.colorScheme) private var colorScheme

    init(
        markdown: String,
        textColor: UIColor = .appReaderBodyText,
        baseFont: UIFont = .appReaderBody,
        adjustsFontForContentSizeCategory: Bool = false,
        scalingTextStyle: UIFont.TextStyle = .body,
        onDigDeeper: ((String) -> Void)? = nil
    ) {
        self.markdown = markdown
        self.textColor = textColor
        self.baseFont = baseFont
        self.adjustsFontForContentSizeCategory = adjustsFontForContentSizeCategory
        self.scalingTextStyle = scalingTextStyle
        self.onDigDeeper = onDigDeeper
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.adjustsFontForContentSizeCategory = adjustsFontForContentSizeCategory
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.setContentHuggingPriority(.defaultLow, for: .horizontal)
        textView.dataDetectorTypes = [.link]
        textView.tintColor = UIColor.appAccent.resolvedColor(with: textView.traitCollection)
        textView.linkTextAttributes = [.underlineStyle: NSUnderlineStyle.single.rawValue]
        textView.onDigDeeper = onDigDeeper
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        uiView.onDigDeeper = onDigDeeper
        uiView.adjustsFontForContentSizeCategory = adjustsFontForContentSizeCategory
        let resolvedTextColor = textColor.resolvedColor(with: uiView.traitCollection)
        let resolvedLinkColor = UIColor.appAccent.resolvedColor(with: uiView.traitCollection)
        let linkAppearanceSignature = context.coordinator.colorSignature(for: resolvedLinkColor)
        let scaledBaseFont = Self.scaledFont(
            baseFont,
            relativeTo: scalingTextStyle,
            compatibleWith: uiView.traitCollection,
            adjustsForContentSizeCategory: adjustsFontForContentSizeCategory
        )
        let renderKey = RenderKey(
            markdown: markdown,
            baseFontName: scaledBaseFont.fontDescriptor.postscriptName,
            baseFontSize: scaledBaseFont.pointSize,
            textColorSignature: context.coordinator.colorSignature(for: resolvedTextColor),
            linkColorSignature: linkAppearanceSignature,
            colorSchemeSignature: String(describing: colorScheme)
        )

        if context.coordinator.lastLinkAppearanceSignature != linkAppearanceSignature {
            context.coordinator.lastLinkAppearanceSignature = linkAppearanceSignature
            uiView.tintColor = resolvedLinkColor
            uiView.linkTextAttributes = [.underlineStyle: NSUnderlineStyle.single.rawValue]
        }

        guard context.coordinator.lastRenderKey != renderKey,
              context.coordinator.pendingRenderKey != renderKey else { return }

        let renderer = MarkdownNSRenderer(
            baseFont: scaledBaseFont,
            textColor: resolvedTextColor,
            traitCollection: uiView.traitCollection
        )
        if let cached = SelectableMarkdownRenderCache.shared.value(for: renderKey) {
            context.coordinator.apply(
                cached,
                key: renderKey,
                to: uiView
            )
            return
        }

        if uiView.attributedText.length == 0 || context.coordinator.lastRenderKey?.markdown != markdown {
            uiView.attributedText = NSAttributedString(
                string: "\u{00a0}",
                attributes: [
                    .font: scaledBaseFont,
                    .foregroundColor: resolvedTextColor
                ]
            )
            uiView.invalidateIntrinsicContentSize()
        }
        context.coordinator.render(
            markdown,
            key: renderKey,
            renderer: renderer,
            into: uiView
        )
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: DigDeeperTextView, context: Context) -> CGSize? {
        guard let width = proposal.width, width.isFinite, width > 0 else { return nil }

        let resolvedTextColor = textColor.resolvedColor(with: uiView.traitCollection)
        let resolvedLinkColor = UIColor.appAccent.resolvedColor(with: uiView.traitCollection)
        let scaledBaseFont = Self.scaledFont(
            baseFont,
            relativeTo: scalingTextStyle,
            compatibleWith: uiView.traitCollection,
            adjustsForContentSizeCategory: adjustsFontForContentSizeCategory
        )
        let renderKey = RenderKey(
            markdown: markdown,
            baseFontName: scaledBaseFont.fontDescriptor.postscriptName,
            baseFontSize: scaledBaseFont.pointSize,
            textColorSignature: context.coordinator.colorSignature(for: resolvedTextColor),
            linkColorSignature: context.coordinator.colorSignature(for: resolvedLinkColor),
            colorSchemeSignature: String(describing: colorScheme)
        )

        if let cache = context.coordinator.cachedSize,
           cache.renderKey == renderKey,
           abs(cache.width - width) < 0.5 {
            return CGSize(width: width, height: cache.height)
        }

        let fittingSize = uiView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))
        context.coordinator.cachedSize = SizeCacheEntry(renderKey: renderKey, width: width, height: fittingSize.height)
        return CGSize(width: width, height: fittingSize.height)
    }

    static func scaledFont(
        _ font: UIFont,
        relativeTo textStyle: UIFont.TextStyle,
        compatibleWith traitCollection: UITraitCollection,
        adjustsForContentSizeCategory: Bool
    ) -> UIFont {
        guard adjustsForContentSizeCategory else { return font }
        return UIFontMetrics(forTextStyle: textStyle).scaledFont(
            for: font,
            compatibleWith: traitCollection
        )
    }

    struct RenderKey: Hashable {
        let markdown: String
        let baseFontName: String
        let baseFontSize: CGFloat
        let textColorSignature: String
        let linkColorSignature: String
        let colorSchemeSignature: String
    }

    struct SizeCacheEntry {
        let renderKey: RenderKey
        let width: CGFloat
        let height: CGFloat
    }

    class Coordinator {
        private final class RenderCancellation {
            private let lock = NSLock()
            private var cancelled = false

            var isCancelled: Bool {
                lock.withLock { cancelled }
            }

            func cancel() {
                lock.withLock {
                    cancelled = true
                }
            }
        }

        var lastRenderKey: RenderKey?
        var pendingRenderKey: RenderKey?
        var lastLinkAppearanceSignature: String?
        var cachedSize: SizeCacheEntry?
        private var renderCancellation: RenderCancellation?

        func render(
            _ markdown: String,
            key renderKey: RenderKey,
            renderer: MarkdownNSRenderer,
            into textView: DigDeeperTextView
        ) {
            renderCancellation?.cancel()
            let cancellation = RenderCancellation()
            renderCancellation = cancellation
            pendingRenderKey = renderKey
            DispatchQueue.global(qos: .userInitiated).async { [weak self, weak textView] in
                guard let self, !cancellation.isCancelled else { return }
                let rendered = SelectableMarkdownRenderCache.shared.value(for: renderKey)
                    ?? renderer.render(markdown)
                guard !cancellation.isCancelled else { return }
                SelectableMarkdownRenderCache.shared.insert(rendered, for: renderKey)
                DispatchQueue.main.async {
                    guard !cancellation.isCancelled,
                          self.pendingRenderKey == renderKey,
                          let textView else { return }
                    self.apply(rendered, key: renderKey, to: textView)
                }
            }
        }

        func apply(
            _ rendered: NSAttributedString,
            key renderKey: RenderKey,
            to textView: DigDeeperTextView
        ) {
            guard lastRenderKey != renderKey else { return }
            if pendingRenderKey != nil, pendingRenderKey != renderKey {
                renderCancellation?.cancel()
            }
            textView.attributedText = rendered
            lastRenderKey = renderKey
            pendingRenderKey = nil
            cachedSize = nil
            textView.invalidateIntrinsicContentSize()
        }

        func colorSignature(for color: UIColor) -> String {
            var red: CGFloat = 0
            var green: CGFloat = 0
            var blue: CGFloat = 0
            var alpha: CGFloat = 0
            if color.getRed(&red, green: &green, blue: &blue, alpha: &alpha) {
                return String(
                    format: "%.4f-%.4f-%.4f-%.4f",
                    red,
                    green,
                    blue,
                    alpha
                )
            }
            return color.description
        }

    }
}

final class SelectableMarkdownRenderCache {
    static let shared = SelectableMarkdownRenderCache()

    private final class CacheKey: NSObject {
        let renderKey: SelectableMarkdownView.RenderKey

        init(_ renderKey: SelectableMarkdownView.RenderKey) {
            self.renderKey = renderKey
        }

        override var hash: Int { renderKey.hashValue }

        override func isEqual(_ object: Any?) -> Bool {
            guard let other = object as? CacheKey else { return false }
            return renderKey == other.renderKey
        }
    }

    private let cache = NSCache<CacheKey, NSAttributedString>()

    init() {
        cache.countLimit = 128
        cache.totalCostLimit = 24 * 1_024 * 1_024
    }

    func value(for key: SelectableMarkdownView.RenderKey) -> NSAttributedString? {
        cache.object(forKey: CacheKey(key))
    }

    func insert(
        _ value: NSAttributedString,
        for key: SelectableMarkdownView.RenderKey
    ) {
        cache.setObject(value, forKey: CacheKey(key), cost: max(value.length * 64, 1))
    }

    func removeAll() {
        cache.removeAllObjects()
    }
}

// MARK: - Markdown → NSAttributedString Renderer

struct MarkdownNSRenderer {
    let baseFont: UIFont
    let textColor: UIColor
    let traitCollection: UITraitCollection

    private enum TableColumnAlignment {
        case leading
        case center
        case trailing
    }

    private struct ParsedTable {
        let headers: [String]
        let alignments: [TableColumnAlignment]
        let rows: [[String]]
    }

    func render(_ markdown: String) -> NSAttributedString {
        let result = NSMutableAttributedString()
        let rawLines = markdown.components(separatedBy: "\n")
        var inCodeBlock = false
        var codeLines: [String] = []
        var i = 0

        while i < rawLines.count {
            let line = rawLines[i]

            // --- Code fence toggle ---
            if line.hasPrefix("```") {
                if inCodeBlock {
                    appendCodeBlock(codeLines.joined(separator: "\n"), to: result)
                    codeLines = []
                    inCodeBlock = false
                } else {
                    inCodeBlock = true
                }
                i += 1
                continue
            }
            if inCodeBlock {
                codeLines.append(line)
                i += 1
                continue
            }

            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // --- Empty line → paragraph spacing ---
            if trimmed.isEmpty {
                appendSpacing(16, to: result)
                i += 1
                continue
            }

            // --- Thematic break ---
            if trimmed.range(of: #"^[-*_]{3,}$"#, options: .regularExpression) != nil {
                appendSpacing(12, to: result)
                i += 1
                continue
            }

            // --- Heading ---
            if let (level, text) = parseHeading(line) {
                let topSpacing: CGFloat = [16, 14, 12, 10, 10, 10][min(level - 1, 5)]
                let bottomSpacing: CGFloat = [8, 6, 6, 4, 4, 4][min(level - 1, 5)]
                if result.length > 0 { appendSpacing(topSpacing, to: result) }
                if level <= 3 { appendHeadingAccentRule(to: result) }
                let rendered = renderInline(text)
                applyHeadingStyle(to: rendered, level: level)
                result.append(rendered)
                appendSpacing(bottomSpacing, to: result)
                i += 1
                continue
            }

            // --- Blockquote ---
            if line.hasPrefix("> ") || line == ">" {
                let text = line.hasPrefix("> ") ? String(line.dropFirst(2)) : ""
                let rendered = renderInline(text)
                applyBlockquoteStyle(to: rendered)
                if result.length > 0 && !result.string.hasSuffix("\n") {
                    result.append(NSAttributedString(string: "\n"))
                }
                result.append(rendered)
                i += 1
                continue
            }

            // --- Unordered list item ---
            if trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") {
                let indent = line.prefix(while: { $0 == " " || $0 == "\t" }).count
                let text = String(trimmed.dropFirst(2))
                let bullet = indent > 0 ? "  ◦ " : "• "
                let rendered = renderInline(bullet + text)
                applyListStyle(to: rendered)
                if result.length > 0 && !result.string.hasSuffix("\n") {
                    result.append(NSAttributedString(string: "\n"))
                }
                result.append(rendered)
                i += 1
                continue
            }

            // --- Ordered list item ---
            if trimmed.range(of: #"^(\d+)\.\s+(.+)$"#, options: .regularExpression) != nil {
                let rendered = renderInline(trimmed)
                applyListStyle(to: rendered)
                if result.length > 0 && !result.string.hasSuffix("\n") {
                    result.append(NSAttributedString(string: "\n"))
                }
                result.append(rendered)
                i += 1
                continue
            }

            // --- GitHub-style table ---
            if let (table, nextIndex) = parseTable(startingAt: i, in: rawLines) {
                appendTable(table, to: result)
                i = nextIndex
                continue
            }

            // --- Regular paragraph text ---
            if result.length > 0 && !result.string.hasSuffix("\n") {
                let previousLine = i > 0
                    ? rawLines[i - 1].trimmingCharacters(in: .whitespaces)
                    : ""
                let followsList = previousLine.hasPrefix("- ")
                    || previousLine.hasPrefix("* ")
                    || previousLine.range(of: #"^(\d+)\.\s+(.+)$"#, options: .regularExpression) != nil
                result.append(NSAttributedString(
                    string: followsList ? "\n" : " ",
                    attributes: defaultAttrs
                ))
            }
            result.append(renderInline(line))
            i += 1
        }

        // Close unclosed code block
        if inCodeBlock && !codeLines.isEmpty {
            appendCodeBlock(codeLines.joined(separator: "\n"), to: result)
        }

        // Trim trailing whitespace/newlines
        let str = result.string
        if let lastNonWhitespace = str.rangeOfCharacter(from: CharacterSet.whitespacesAndNewlines.inverted, options: .backwards) {
            let end = str.distance(from: str.startIndex, to: lastNonWhitespace.upperBound)
            if end < result.length {
                result.deleteCharacters(in: NSRange(location: end, length: result.length - end))
            }
        }

        return result
    }

    // MARK: - Block Helpers

    private func parseHeading(_ line: String) -> (level: Int, text: String)? {
        guard line.hasPrefix("#") else { return nil }
        let hashes = line.prefix(while: { $0 == "#" })
        let level = hashes.count
        guard level <= 6 else { return nil }
        let rest = line.dropFirst(level)
        guard rest.hasPrefix(" ") else { return nil }
        return (level, String(rest.dropFirst()))
    }

    private func appendCodeBlock(_ code: String, to result: NSMutableAttributedString) {
        let codeFont = UIFont.appSans(size: baseFont.pointSize * 0.85)
        // Palette surface, not a hardcoded grey — a cool chip reads as a patch of the old
        // theme inside warm article text.
        let bgColor = ReaderPalette.colors.surfaceTertiary.uiColor(for: traitCollection)

        if result.length > 0 {
            result.append(NSAttributedString(string: "\n"))
        }

        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 3
        paragraph.paragraphSpacingBefore = 8
        paragraph.paragraphSpacing = 10

        let attrs: [NSAttributedString.Key: Any] = [
            .font: codeFont,
            .foregroundColor: textColor,
            .backgroundColor: bgColor,
            .paragraphStyle: paragraph
        ]
        result.append(NSAttributedString(string: "  " + code + "  ", attributes: attrs))
        result.append(NSAttributedString(string: "\n"))
    }

    private func appendSpacing(_ points: CGFloat, to result: NSMutableAttributedString) {
        let spacingFont = baseFont.withSize(points * 0.75)
        result.append(NSAttributedString(string: "\n", attributes: [.font: spacingFont]))
    }

    private func parseTable(startingAt index: Int, in lines: [String]) -> (ParsedTable, Int)? {
        guard index + 1 < lines.count else { return nil }
        guard let headers = parseTableRow(lines[index]) else { return nil }
        guard headers.count >= 2 else { return nil }
        guard let alignments = parseTableSeparatorRow(lines[index + 1]) else { return nil }
        guard alignments.count == headers.count else { return nil }

        var rows: [[String]] = []
        var currentIndex = index + 2

        while currentIndex < lines.count {
            let line = lines[currentIndex]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                break
            }
            guard let parsedRow = parseTableRow(line) else { break }
            rows.append(normalizeTableRow(parsedRow, columnCount: headers.count))
            currentIndex += 1
        }

        let table = ParsedTable(
            headers: normalizeTableRow(headers, columnCount: headers.count),
            alignments: alignments,
            rows: rows
        )
        return (table, currentIndex)
    }

    private func parseTableRow(_ line: String) -> [String]? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed.contains("|") else { return nil }

        var cells: [String] = []
        var current = ""
        var isEscaped = false

        for char in trimmed {
            if isEscaped {
                current.append(char)
                isEscaped = false
                continue
            }
            if char == "\\" {
                isEscaped = true
                continue
            }
            if char == "|" {
                cells.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
                continue
            }
            current.append(char)
        }

        if isEscaped {
            current.append("\\")
        }
        cells.append(current.trimmingCharacters(in: .whitespaces))

        if trimmed.hasPrefix("|"), !cells.isEmpty {
            cells.removeFirst()
        }
        if trimmed.hasSuffix("|"), !cells.isEmpty {
            cells.removeLast()
        }

        return cells.count >= 2 ? cells : nil
    }

    private func parseTableSeparatorRow(_ line: String) -> [TableColumnAlignment]? {
        guard let cells = parseTableRow(line) else { return nil }

        var alignments: [TableColumnAlignment] = []
        for cell in cells {
            let token = cell.replacingOccurrences(of: " ", with: "")
            guard token.range(of: #"^:?-{3,}:?$"#, options: .regularExpression) != nil else {
                return nil
            }
            if token.hasPrefix(":"), token.hasSuffix(":") {
                alignments.append(.center)
                continue
            }
            if token.hasSuffix(":") {
                alignments.append(.trailing)
                continue
            }
            alignments.append(.leading)
        }

        return alignments
    }

    private func normalizeTableRow(_ row: [String], columnCount: Int) -> [String] {
        let padded = Array(row.prefix(columnCount))
        if padded.count == columnCount {
            return padded
        }
        return padded + Array(repeating: "", count: columnCount - padded.count)
    }

    private func appendTable(_ table: ParsedTable, to result: NSMutableAttributedString) {
        let rows = ([table.headers] + table.rows).map { row in
            row.map(sanitizeTableCell)
        }
        let widths = (0..<table.headers.count).map { index in
            rows.map { $0[index].count }.max() ?? 0
        }

        if result.length > 0, !result.string.hasSuffix("\n") {
            result.append(NSAttributedString(string: "\n"))
        }

        let tableLines = [
            renderTableLine(table.headers, widths: widths, alignments: table.alignments),
            renderTableSeparator(widths: widths, alignments: table.alignments)
        ] + table.rows.map {
            renderTableLine($0, widths: widths, alignments: table.alignments)
        }

        let tableText = tableLines.joined(separator: "\n")
        let tableAttrs: [NSAttributedString.Key: Any] = [
            .font: UIFont.appSans(size: baseFont.pointSize * 0.86),
            .foregroundColor: textColor
        ]

        let rendered = NSMutableAttributedString(string: tableText, attributes: tableAttrs)
        applyTableStyle(to: rendered)
        result.append(rendered)
        result.append(NSAttributedString(string: "\n"))
    }

    private func renderTableLine(
        _ row: [String],
        widths: [Int],
        alignments: [TableColumnAlignment]
    ) -> String {
        zip(zip(row, widths), alignments)
            .map { item in
                let ((cell, width), alignment) = item
                let text = cell
                let padding = max(width - text.count, 0)
                switch alignment {
                case .leading:
                    return " " + text + String(repeating: " ", count: padding) + " "
                case .center:
                    let left = padding / 2
                    let right = padding - left
                    return " " + String(repeating: " ", count: left) + text + String(repeating: " ", count: right) + " "
                case .trailing:
                    return " " + String(repeating: " ", count: padding) + text + " "
                }
            }
            .joined(separator: "|")
    }

    private func renderTableSeparator(
        widths: [Int],
        alignments: [TableColumnAlignment]
    ) -> String {
        zip(widths, alignments)
            .map { width, alignment in
                let dashCount = max(width, 3)
                switch alignment {
                case .leading:
                    return " " + String(repeating: "-", count: dashCount) + " "
                case .center:
                    return ":" + String(repeating: "-", count: dashCount) + ":"
                case .trailing:
                    return String(repeating: "-", count: dashCount + 1) + ":"
                }
            }
            .joined(separator: "|")
    }

    private func sanitizeTableCell(_ text: String) -> String {
        let rendered = renderInline(text).string
        return rendered.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Block Styling

    private func applyHeadingStyle(to attrStr: NSMutableAttributedString, level: Int) {
        let range = NSRange(location: 0, length: attrStr.length)
        let scales: [CGFloat] = [1.36, 1.22, 1.12, 1.02, 0.94, 0.88]
        let scale = scales[min(level - 1, 5)]
        // Option A: section headings are serif (matching the reader title), with a
        // accent rule rendered directly above them (see appendHeadingAccentRule).
        let weight: UIFont.Weight = .semibold
        let styledHeadingFont = UIFont.appSerif(size: baseFont.pointSize * scale, weight: weight)
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineHeightMultiple = 1.08
        // Levels 1–3 carry an accent rule above, so keep the gap to the rule tight.
        paragraph.paragraphSpacingBefore = level <= 3 ? 0 : 4
        paragraph.paragraphSpacing = level <= 2 ? 7 : 5

        // Rebuild the heading font per run so inline emphasis (bold/italic) survives,
        // rather than blanket-overwriting the per-run fonts from renderInline.
        attrStr.enumerateAttribute(.font, in: range) { value, subRange, _ in
            guard let runFont = value as? UIFont else {
                attrStr.addAttribute(.font, value: styledHeadingFont, range: subRange)
                return
            }
            let runTraits = runFont.fontDescriptor.symbolicTraits.intersection([.traitItalic, .traitBold])
            guard !runTraits.isEmpty,
                  let mergedDescriptor = styledHeadingFont.fontDescriptor.withSymbolicTraits(
                      styledHeadingFont.fontDescriptor.symbolicTraits.union(runTraits)
                  ) else {
                attrStr.addAttribute(.font, value: styledHeadingFont, range: subRange)
                return
            }
            let mergedFont = UIFont(descriptor: mergedDescriptor, size: styledHeadingFont.pointSize)
            attrStr.addAttribute(.font, value: mergedFont, range: subRange)
        }
        attrStr.addAttribute(.paragraphStyle, value: paragraph, range: range)
        attrStr.addAttribute(.foregroundColor, value: textColor, range: range)
    }

    /// Option A editorial accent: a short accent rule rendered on its own line
    /// directly above a section heading, anchoring it against the body copy.
    private func appendHeadingAccentRule(to result: NSMutableAttributedString) {
        if result.length > 0, !result.string.hasSuffix("\n") {
            result.append(NSAttributedString(string: "\n"))
        }

        let width: CGFloat = 26
        let height: CGFloat = 2.5
        let accent = UIColor.appAccent.resolvedColor(with: traitCollection)
        let image = UIGraphicsImageRenderer(size: CGSize(width: width, height: height)).image { _ in
            accent.setFill()
            UIBezierPath(
                roundedRect: CGRect(x: 0, y: 0, width: width, height: height),
                cornerRadius: height / 2
            ).fill()
        }

        let attachment = NSTextAttachment()
        attachment.image = image
        attachment.bounds = CGRect(x: 0, y: 0, width: width, height: height)

        let ruleParagraph = NSMutableParagraphStyle()
        ruleParagraph.lineSpacing = 0
        ruleParagraph.paragraphSpacing = 6

        let rule = NSMutableAttributedString(attachment: attachment)
        rule.append(NSAttributedString(string: "\n"))
        rule.addAttribute(
            .paragraphStyle,
            value: ruleParagraph,
            range: NSRange(location: 0, length: rule.length)
        )
        result.append(rule)
    }

    private func applyBlockquoteStyle(to attrStr: NSMutableAttributedString) {
        let range = NSRange(location: 0, length: attrStr.length)
        let quoteColor = UIColor.appOnSurfaceSecondary.resolvedColor(with: traitCollection)
        attrStr.addAttribute(.foregroundColor, value: quoteColor, range: range)
        attrStr.insert(NSAttributedString(string: "  "), at: 0)
        attrStr.insert(NSAttributedString(string: "| ", attributes: [
            .foregroundColor: UIColor.appAccent.resolvedColor(with: traitCollection),
            .font: UIFont.appSans(size: baseFont.pointSize, weight: .semibold)
        ]), at: 0)

        // Apply italic where possible
        let updatedRange = NSRange(location: 0, length: attrStr.length)
        attrStr.enumerateAttribute(.font, in: updatedRange) { value, subRange, _ in
            if let font = value as? UIFont,
               let italic = font.withTraits(.traitItalic) {
                attrStr.addAttribute(.font, value: italic, range: subRange)
            }
        }

        let para = NSMutableParagraphStyle()
        para.firstLineHeadIndent = 0
        para.headIndent = 18
        para.lineSpacing = 3
        para.paragraphSpacingBefore = 6
        para.paragraphSpacing = 8
        attrStr.addAttribute(.paragraphStyle, value: para, range: updatedRange)
    }

    private func applyListStyle(to attrStr: NSMutableAttributedString) {
        let range = NSRange(location: 0, length: attrStr.length)
        let para = NSMutableParagraphStyle()
        // Match wrapped lines to the first character after the bullet instead
        // of pushing them an extra word-width to the right.
        para.headIndent = 15
        para.firstLineHeadIndent = 0
        para.lineSpacing = 3
        para.paragraphSpacing = 6
        attrStr.addAttribute(.paragraphStyle, value: para, range: range)
    }

    private func applyTableStyle(to attrStr: NSMutableAttributedString) {
        let range = NSRange(location: 0, length: attrStr.length)
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 2
        paragraph.paragraphSpacingBefore = 8
        paragraph.paragraphSpacing = 10
        attrStr.addAttribute(.paragraphStyle, value: paragraph, range: range)
    }

    // MARK: - Inline Rendering

    private var defaultAttrs: [NSAttributedString.Key: Any] {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineHeightMultiple = 1.18
        paragraph.paragraphSpacing = 7
        return [
            .font: baseFont,
            .foregroundColor: textColor,
            .paragraphStyle: paragraph
        ]
    }

    /// Renders inline markdown (bold, italic, code, links, strikethrough)
    /// using Apple's `AttributedString` parser.
    private func renderInline(_ text: String) -> NSMutableAttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        guard let parsed = try? AttributedString(markdown: text, options: options) else {
            return NSMutableAttributedString(string: text, attributes: defaultAttrs)
        }

        let result = NSMutableAttributedString()
        for run in parsed.runs {
            let content = String(parsed[run.range].characters)
            var attrs = defaultAttrs
            var font = baseFont

            if let inlineIntent = run.inlinePresentationIntent {
                if inlineIntent.contains(.stronglyEmphasized) {
                    let descriptor = font.fontDescriptor.addingAttributes([
                        .traits: [UIFontDescriptor.TraitKey.weight: UIFont.Weight.semibold.rawValue]
                    ])
                    font = UIFont(descriptor: descriptor, size: font.pointSize)
                }
                if inlineIntent.contains(.emphasized) {
                    font = font.withTraits(.traitItalic) ?? font
                }
                if inlineIntent.contains(.code) {
                    font = UIFont.appSans(size: baseFont.pointSize * 0.88)
                    // One rung above the block-code fill so small inline chips stay visible.
                    attrs[.backgroundColor] =
                        ReaderPalette.colors.surfaceContainer.uiColor(for: traitCollection)
                }
                if inlineIntent.contains(.strikethrough) {
                    attrs[.strikethroughStyle] = NSUnderlineStyle.single.rawValue
                }
                // Handle bold+italic combo
                if inlineIntent.contains(.stronglyEmphasized) && inlineIntent.contains(.emphasized) {
                    let descriptor = baseFont.fontDescriptor.addingAttributes([
                        .traits: [UIFontDescriptor.TraitKey.weight: UIFont.Weight.semibold.rawValue]
                    ])
                    if let boldItalic = UIFont(descriptor: descriptor, size: baseFont.pointSize)
                        .withTraits(.traitItalic) {
                        font = boldItalic
                    }
                }
            }

            attrs[.font] = font

            // Links
            if let link = run.link {
                attrs[.link] = link
                // UITextView handles link color via linkTextAttributes
            }

            result.append(NSAttributedString(string: content, attributes: attrs))
        }

        return result
    }
}

// MARK: - UIFont Traits Helper

private extension UIFont {
    func withTraits(_ traits: UIFontDescriptor.SymbolicTraits) -> UIFont? {
        guard let descriptor = fontDescriptor.withSymbolicTraits(
            fontDescriptor.symbolicTraits.union(traits)
        ) else { return nil }
        return UIFont(descriptor: descriptor, size: 0)
    }
}
