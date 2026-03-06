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
    var onDigDeeper: ((String) -> Void)?

    init(
        markdown: String,
        textColor: UIColor = .label,
        baseFont: UIFont = .preferredFont(forTextStyle: .callout),
        onDigDeeper: ((String) -> Void)? = nil
    ) {
        self.markdown = markdown
        self.textColor = textColor
        self.baseFont = baseFont
        self.onDigDeeper = onDigDeeper
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onDigDeeper: onDigDeeper)
    }

    func makeUIView(context: Context) -> DigDeeperTextView {
        let textView = DigDeeperTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.isScrollEnabled = false
        textView.backgroundColor = .clear
        textView.textContainerInset = .zero
        textView.textContainer.lineFragmentPadding = 0
        textView.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        textView.setContentHuggingPriority(.defaultLow, for: .horizontal)
        textView.dataDetectorTypes = [.link]
        textView.linkTextAttributes = [
            .foregroundColor: UIColor.link,
            .underlineStyle: NSUnderlineStyle.single.rawValue
        ]
        textView.onDigDeeper = context.coordinator.onDigDeeper
        return textView
    }

    func updateUIView(_ uiView: DigDeeperTextView, context: Context) {
        uiView.onDigDeeper = context.coordinator.onDigDeeper
        let rendered = MarkdownNSRenderer(baseFont: baseFont, textColor: textColor).render(markdown)
        uiView.attributedText = rendered
        uiView.invalidateIntrinsicContentSize()
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: DigDeeperTextView, context: Context) -> CGSize? {
        let width = proposal.width ?? UIScreen.main.bounds.width
        let fittingSize = uiView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))
        return CGSize(width: width, height: fittingSize.height)
    }

    class Coordinator {
        var onDigDeeper: ((String) -> Void)?
        init(onDigDeeper: ((String) -> Void)?) {
            self.onDigDeeper = onDigDeeper
        }
    }
}

// MARK: - Markdown → NSAttributedString Renderer

struct MarkdownNSRenderer {
    let baseFont: UIFont
    let textColor: UIColor

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
                // Continuation of same paragraph — add space
                result.append(NSAttributedString(string: " ", attributes: defaultAttrs))
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
        let codeFont = UIFont.monospacedSystemFont(ofSize: baseFont.pointSize * 0.85, weight: .regular)
        let bgColor = UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.14, green: 0.15, blue: 0.17, alpha: 1)
                : UIColor(red: 0.95, green: 0.96, blue: 0.97, alpha: 1)
        }

        if result.length > 0 {
            result.append(NSAttributedString(string: "\n"))
        }

        let attrs: [NSAttributedString.Key: Any] = [
            .font: codeFont,
            .foregroundColor: textColor,
            .backgroundColor: bgColor
        ]
        result.append(NSAttributedString(string: code, attributes: attrs))
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
        let rows = [table.headers] + table.rows
        let widths = (0..<table.headers.count).map { index in
            rows.map { plainTextWidth(of: $0[index]) }.max() ?? 0
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
            .font: UIFont.monospacedSystemFont(ofSize: baseFont.pointSize * 0.86, weight: .regular),
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
                let text = sanitizeTableCell(cell)
                let padding = max(width - plainTextWidth(of: text), 0)
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

    private func plainTextWidth(of text: String) -> Int {
        sanitizeTableCell(text).count
    }

    // MARK: - Block Styling

    private func applyHeadingStyle(to attrStr: NSMutableAttributedString, level: Int) {
        let range = NSRange(location: 0, length: attrStr.length)
        let scales: [CGFloat] = [1.25, 1.15, 1.05, 1.0, 0.9, 0.85]
        let scale = scales[min(level - 1, 5)]
        let weight: UIFont.Weight = level <= 1 ? .bold : .semibold
        let headingFont = UIFont.systemFont(ofSize: baseFont.pointSize * scale, weight: weight)

        attrStr.addAttribute(.font, value: headingFont, range: range)
        if level == 6 {
            attrStr.addAttribute(.foregroundColor, value: UIColor.secondaryLabel, range: range)
        }
    }

    private func applyBlockquoteStyle(to attrStr: NSMutableAttributedString) {
        let range = NSRange(location: 0, length: attrStr.length)
        attrStr.addAttribute(.foregroundColor, value: UIColor.secondaryLabel, range: range)

        // Apply italic where possible
        attrStr.enumerateAttribute(.font, in: range) { value, subRange, _ in
            if let font = value as? UIFont,
               let italic = font.withTraits(.traitItalic) {
                attrStr.addAttribute(.font, value: italic, range: subRange)
            }
        }

        let para = NSMutableParagraphStyle()
        para.firstLineHeadIndent = 14
        para.headIndent = 14
        attrStr.addAttribute(.paragraphStyle, value: para, range: range)
    }

    private func applyListStyle(to attrStr: NSMutableAttributedString) {
        let range = NSRange(location: 0, length: attrStr.length)
        let para = NSMutableParagraphStyle()
        para.headIndent = 20
        para.firstLineHeadIndent = 0
        para.paragraphSpacing = 3
        attrStr.addAttribute(.paragraphStyle, value: para, range: range)
    }

    private func applyTableStyle(to attrStr: NSMutableAttributedString) {
        let range = NSRange(location: 0, length: attrStr.length)
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 2
        paragraph.paragraphSpacing = 6
        attrStr.addAttribute(.paragraphStyle, value: paragraph, range: range)
    }

    // MARK: - Inline Rendering

    private var defaultAttrs: [NSAttributedString.Key: Any] {
        [.font: baseFont, .foregroundColor: textColor]
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
                    font = UIFont.systemFont(ofSize: font.pointSize, weight: .semibold)
                }
                if inlineIntent.contains(.emphasized) {
                    font = font.withTraits(.traitItalic) ?? font
                }
                if inlineIntent.contains(.code) {
                    font = UIFont.monospacedSystemFont(ofSize: baseFont.pointSize * 0.88, weight: .regular)
                    let bgColor = UIColor { traits in
                        traits.userInterfaceStyle == .dark
                            ? UIColor(red: 0.2, green: 0.21, blue: 0.23, alpha: 1)
                            : UIColor(red: 0.95, green: 0.96, blue: 0.97, alpha: 1)
                    }
                    attrs[.backgroundColor] = bgColor
                }
                if inlineIntent.contains(.strikethrough) {
                    attrs[.strikethroughStyle] = NSUnderlineStyle.single.rawValue
                }
                // Handle bold+italic combo
                if inlineIntent.contains(.stronglyEmphasized) && inlineIntent.contains(.emphasized) {
                    if let boldItalic = UIFont.systemFont(ofSize: baseFont.pointSize, weight: .semibold)
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
