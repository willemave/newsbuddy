//
//  ShareMarkdownBuilder.swift
//  newsly
//

import Foundation
import UIKit
import UniformTypeIdentifiers

enum ShareContentOption {
    case light
    case medium
    case full
}

struct ShareMarkdownBuilder {
    let content: ContentDetail
    let contentBody: ContentBody?

    var shareURLString: String? {
        resolvedShareURLString(for: content)
    }

    func markdown(for option: ShareContentOption) -> String? {
        switch option {
        case .light:
            return nil
        case .medium:
            return buildMediumMarkdown()
        case .full:
            return buildFullMarkdown() ?? buildMediumMarkdown()
        }
    }

    private func uniqueNonEmpty(_ values: [String]) -> [String] {
        var seen: Set<String> = []
        var result: [String] = []

        for value in values {
            guard let normalized = nonEmptyTrimmed(value) else { continue }
            let key = normalized.lowercased()
            if seen.insert(key).inserted {
                result.append(normalized)
            }
        }

        return result
    }

    private func resolvedShareURLString(for content: ContentDetail) -> String? {
        if content.contentType == .news,
           let articleURL = content.resolvedNewsArticleURL {
            return articleURL
        }
        return nonEmptyTrimmed(content.url)
    }

    private func resolvedOverviewText(for content: ContentDetail) -> String? {
        if let overview = nonEmptyTrimmed(content.structuredSummary?.overview) {
            return overview
        }
        if let hook = nonEmptyTrimmed(content.interleavedSummaryV2?.hook) {
            return hook
        }
        if let hook = nonEmptyTrimmed(content.interleavedSummary?.hook) {
            return hook
        }
        if let editorialSummary = content.editorialSummary,
           let firstParagraph = editorialSummary.narrativeParagraphs.first,
           let narrative = nonEmptyTrimmed(firstParagraph) {
            return narrative
        }
        if content.contentType == .news {
            return nil
        }
        if let newsSummary = content.resolvedNewsSummaryText {
            return newsSummary
        }
        return nil
    }

    private func resolvedKeyPointTexts(for content: ContentDetail) -> [String] {
        var points: [String] = []

        if let structuredSummary = content.structuredSummary {
            points.append(contentsOf: structuredSummary.bulletPoints.map(\.text))
        }
        if let interleavedSummaryV2 = content.interleavedSummaryV2 {
            points.append(contentsOf: interleavedSummaryV2.keyPoints.map(\.text))
        }
        if let interleavedSummary = content.interleavedSummary {
            points.append(contentsOf: interleavedSummary.insights.map(\.insight))
        }
        if let bulletedSummary = content.bulletedSummary {
            points.append(contentsOf: bulletedSummary.points.map(\.text))
        }
        if let editorialSummary = content.editorialSummary {
            points.append(contentsOf: editorialSummary.keyPoints.map(\.point))
        }
        points.append(contentsOf: content.bulletPoints.map(\.text))

        if content.contentType == .news {
            points.append(contentsOf: content.resolvedNewsKeyPoints)
        }

        if points.isEmpty {
            if let summary = resolvedOverviewText(for: content) {
                points = [summary]
            }
        }

        return uniqueNonEmpty(points)
    }

    private func resolvedQuoteTexts(for content: ContentDetail) -> [String] {
        var quotes: [String] = []

        if let structuredSummary = content.structuredSummary {
            quotes.append(contentsOf: structuredSummary.quotes.map(\.text))
        }
        if let interleavedSummaryV2 = content.interleavedSummaryV2 {
            quotes.append(contentsOf: interleavedSummaryV2.quotes.map(\.text))
        }
        if let interleavedSummary = content.interleavedSummary {
            quotes.append(
                contentsOf: interleavedSummary.insights.compactMap { insight in
                    nonEmptyTrimmed(insight.supportingQuote)
                }
            )
        }
        if let bulletedSummary = content.bulletedSummary {
            for point in bulletedSummary.points {
                quotes.append(contentsOf: point.quotes.map(\.text))
            }
        }
        if let editorialSummary = content.editorialSummary {
            quotes.append(contentsOf: editorialSummary.quotes.map(\.text))
        }
        quotes.append(contentsOf: content.quotes.map(\.text))

        return uniqueNonEmpty(quotes)
    }

    private func markdownHeading(_ title: String, level: Int) -> String {
        let clampedLevel = min(max(level, 1), 6)
        return String(repeating: "#", count: clampedLevel) + " " + title
    }

    private func buildLongformArtifactSummaryMarkdown(
        _ artifact: LongformArtifactEnvelope,
        headingLevel: Int
    ) -> String? {
        let payload = artifact.artifact.payload
        var sections: [String] = []

        if let summaryText = nonEmptyTrimmed(payload.overview) ?? nonEmptyTrimmed(artifact.oneLine) {
            sections.append("\(markdownHeading("Summary", level: headingLevel))\n\(summaryText)")
        }

        if let takeaway = nonEmptyTrimmed(payload.takeaway) {
            sections.append("\(markdownHeading("Takeaway", level: headingLevel))\n\(takeaway)")
        }

        let keyPointLines = payload.keyPoints.compactMap { point -> String? in
            guard let heading = nonEmptyTrimmed(point.heading),
                  let content = nonEmptyTrimmed(point.content) else {
                return nil
            }
            return "- \(heading): \(content)"
        }
        if !keyPointLines.isEmpty {
            sections.append(
                "\(markdownHeading("Key Points", level: headingLevel))\n"
                + keyPointLines.joined(separator: "\n")
            )
        }

        let quoteBlocks = payload.quotes.compactMap { quote -> String? in
            guard let text = nonEmptyTrimmed(quote.text) else { return nil }
            var block = "> \(text)"
            if let attribution = nonEmptyTrimmed(quote.attribution) {
                block += "\n> - \(attribution)"
            }
            return block
        }
        if !quoteBlocks.isEmpty {
            sections.append(
                "\(markdownHeading("Source Quotes", level: headingLevel))\n"
                + quoteBlocks.joined(separator: "\n\n")
            )
        }

        let extraSections = LongformExtrasSection.orderedSections(from: payload.extrasRaw)
        let extraHeadingLevel = min(headingLevel + 1, 6)
        let extraBlocks = extraSections.compactMap { section -> String? in
            let items = uniqueNonEmpty(section.items)
            guard !items.isEmpty else { return nil }
            let bullets = items.map { "- \($0)" }.joined(separator: "\n")
            return "\(markdownHeading(section.title, level: extraHeadingLevel))\n\(bullets)"
        }
        if !extraBlocks.isEmpty {
            sections.append(
                "\(markdownHeading("Extra", level: headingLevel))\n\n"
                + extraBlocks.joined(separator: "\n\n")
            )
        }

        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }

    private func buildFullMarkdown() -> String? {
        var fullText = "# \(content.displayTitle)\n\n"

        if let source = content.source { fullText += "Source: \(source)\n" }
        if let pubDate = content.publicationDate { fullText += "Published: \(pubDate)\n" }
        if let shareURL = resolvedShareURLString(for: content) {
            fullText += "URL: \(shareURL)\n"
        }
        fullText += "\n---\n\n"

        let overview = resolvedOverviewText(for: content)
        let keyPoints = resolvedKeyPointTexts(for: content)
        let quotes = resolvedQuoteTexts(for: content)
        let hasTemplateSummaryData =
            overview != nil
            || !keyPoints.isEmpty
            || !quotes.isEmpty
            || content.bulletedSummary != nil
            || content.interleavedSummary != nil
            || content.interleavedSummaryV2 != nil
            || content.editorialSummary != nil

        let longformArtifactSummaryMarkdown = content.longformArtifact.flatMap {
            buildLongformArtifactSummaryMarkdown($0, headingLevel: 3)
        }

        if let longformArtifactSummaryMarkdown {
            fullText += "## Summary\n\n"
            fullText += longformArtifactSummaryMarkdown
            fullText += "\n\n---\n\n"
        } else if hasTemplateSummaryData {
            fullText += "## Summary\n\n"

            if let overview {
                fullText += "### Overview\n\(overview)\n\n"
            }

            if let editorialSummary = content.editorialSummary,
               !editorialSummary.narrativeParagraphs.isEmpty {
                fullText += "### Narrative\n"
                fullText += editorialSummary.narrativeParagraphs.joined(separator: "\n\n")
                fullText += "\n\n"
            }

            if !keyPoints.isEmpty {
                fullText += "### Key Points\n"
                fullText += keyPoints.map { "- \($0)" }.joined(separator: "\n")
                fullText += "\n\n"
            }

            if let interleavedSummaryV2 = content.interleavedSummaryV2,
               !interleavedSummaryV2.topics.isEmpty {
                let topicBlocks = interleavedSummaryV2.topics.compactMap { topic -> String? in
                    let bullets = topic.bullets
                        .compactMap { nonEmptyTrimmed($0.text) }
                        .map { "  - \($0)" }
                        .joined(separator: "\n")
                    guard !bullets.isEmpty else { return nil }
                    return "- \(topic.topic)\n\(bullets)"
                }
                if !topicBlocks.isEmpty {
                    fullText += "### Topic Breakdown\n"
                    fullText += topicBlocks.joined(separator: "\n")
                    fullText += "\n\n"
                }
            }

            if let interleavedSummary = content.interleavedSummary,
               !interleavedSummary.insights.isEmpty {
                let insightLines = interleavedSummary.insights.compactMap { insight -> String? in
                    guard let text = nonEmptyTrimmed(insight.insight) else { return nil }
                    if let topic = nonEmptyTrimmed(insight.topic) {
                        return "- \(topic): \(text)"
                    }
                    return "- \(text)"
                }
                if !insightLines.isEmpty {
                    fullText += "### Insights\n"
                    fullText += insightLines.joined(separator: "\n")
                    fullText += "\n\n"
                }
            }

            if let bulletedSummary = content.bulletedSummary, !bulletedSummary.points.isEmpty {
                let pointDetails = bulletedSummary.points.compactMap { point -> String? in
                    guard let text = nonEmptyTrimmed(point.text),
                          let detail = nonEmptyTrimmed(point.detail) else {
                        return nil
                    }
                    var block = "- \(text)\n  \(detail)"
                    if let quote = point.quotes.first,
                       let quoteText = nonEmptyTrimmed(quote.text) {
                        block += "\n  > \(quoteText)"
                    }
                    return block
                }
                if !pointDetails.isEmpty {
                    fullText += "### Point Details\n"
                    fullText += pointDetails.joined(separator: "\n")
                    fullText += "\n\n"
                }
            }

            if !quotes.isEmpty {
                fullText += "### Notable Quotes\n"
                fullText += quotes.map { "> \($0)" }.joined(separator: "\n")
                fullText += "\n\n"
            }

            fullText += "---\n\n"
        }

        if let contentBody {
            fullText += content.contentType == .podcast ? "## Full Transcript\n\n" : "## Full Article\n\n"
            fullText += contentBody.text
        } else if content.contentType == .podcast,
                  let podcastMetadata = content.podcastMetadata,
                  let transcript = podcastMetadata.transcript {
            fullText += "## Full Transcript\n\n" + transcript
        } else if let fullMarkdown = content.fullMarkdown {
            fullText += (content.contentType == .podcast ? "## Transcript\n\n" : "## Full Article\n\n")
            fullText += fullMarkdown
        }
        return fullText
    }

    private func buildMediumMarkdown() -> String? {
        var sections: [String] = []
        sections.append("# \(content.displayTitle)")

        let longformArtifactSummaryMarkdown = content.longformArtifact.flatMap {
            buildLongformArtifactSummaryMarkdown($0, headingLevel: 2)
        }

        if let longformArtifactSummaryMarkdown {
            sections.append(longformArtifactSummaryMarkdown)
        } else {
            if let overview = resolvedOverviewText(for: content) {
                sections.append("## Summary\n\(overview)")
            }

            let keyPoints = resolvedKeyPointTexts(for: content)
            if !keyPoints.isEmpty {
                let bullets = keyPoints.map { "- \($0)" }.joined(separator: "\n")
                sections.append("## Key Points\n\(bullets)")
            }

            let quotes = resolvedQuoteTexts(for: content)
            if !quotes.isEmpty {
                let quoteText = quotes.map { "> \($0)" }.joined(separator: "\n")
                sections.append("## Quotes\n\(quoteText)")
            }
        }

        if let shareURL = resolvedShareURLString(for: content) {
            sections.append("Link: \(shareURL)")
        }

        guard sections.count > 1 else { return nil }
        return sections.joined(separator: "\n\n")
    }
}

// MARK: - Custom Item Provider for Markdown Sharing

class MarkdownItemProvider: NSObject, UIActivityItemSource {
    private let markdown: String
    private let subject: String?

    init(markdown: String, subject: String? = nil) {
        self.markdown = markdown
        self.subject = subject
        super.init()
    }

    func activityViewControllerPlaceholderItem(_ activityViewController: UIActivityViewController) -> Any {
        return markdown
    }

    func activityViewController(
        _ activityViewController: UIActivityViewController,
        itemForActivityType activityType: UIActivity.ActivityType?
    ) -> Any? {
        switch shareActivityKind(activityType) {
        case .mail:
            return convertMarkdownToHTML(markdown).data(using: .utf8)
        case .gmail:
            return gmailFriendlyText(markdown)
        case .other:
            return markdown
        }
    }

    func activityViewController(
        _ activityViewController: UIActivityViewController,
        subjectForActivityType activityType: UIActivity.ActivityType?
    ) -> String {
        if let subject, !subject.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return subject
        }
        return markdown
            .components(separatedBy: .newlines)
            .first(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    func activityViewController(
        _ activityViewController: UIActivityViewController,
        dataTypeIdentifierForActivityType activityType: UIActivity.ActivityType?
    ) -> String {
        switch shareActivityKind(activityType) {
        case .mail:
            return UTType.html.identifier
        case .gmail, .other:
            return UTType.plainText.identifier
        }
    }

    private enum ShareActivityKind {
        case mail
        case gmail
        case other
    }

    private func shareActivityKind(_ activityType: UIActivity.ActivityType?) -> ShareActivityKind {
        if activityType == .mail {
            return .mail
        }

        guard let rawValue = activityType?.rawValue.lowercased() else {
            return .other
        }

        if rawValue.contains("gmail") {
            return .gmail
        }

        return .other
    }

    private func convertMarkdownToHTML(_ markdown: String) -> String {
        var html = "<html><body style='font-family: \(AppFontFamily.sans), -apple-system, sans-serif; font-size: 14px; line-height: 1.6;'>"

        let paragraphs = markdown.components(separatedBy: "\n\n")

        for paragraph in paragraphs {
            var processedParagraph = paragraph

            if processedParagraph.hasPrefix("### ") {
                processedParagraph = "<h3>" + processedParagraph.dropFirst(4) + "</h3>"
            } else if processedParagraph.hasPrefix("## ") {
                processedParagraph = "<h2>" + processedParagraph.dropFirst(3) + "</h2>"
            } else if processedParagraph.hasPrefix("# ") {
                processedParagraph = "<h1>" + processedParagraph.dropFirst(2) + "</h1>"
            } else if processedParagraph.hasPrefix("---") {
                processedParagraph = "<hr/>"
            } else if processedParagraph.contains("\n- ") || processedParagraph.hasPrefix("- ") {
                let items = processedParagraph.components(separatedBy: "\n").filter { $0.hasPrefix("- ") }
                let listItems = items.map { "<li>" + $0.dropFirst(2) + "</li>" }.joined()
                processedParagraph = "<ul>" + listItems + "</ul>"
            } else if processedParagraph.contains("\n> ") || processedParagraph.hasPrefix("> ") {
                let quotes = processedParagraph.components(separatedBy: "\n").filter { $0.hasPrefix("> ") }
                let quoteText = quotes.map { String($0.dropFirst(2)) }.joined(separator: "<br/>")
                processedParagraph = "<blockquote style='border-left: 3px solid #ccc; padding-left: 10px; margin: 10px 0;'>" + quoteText + "</blockquote>"
            } else if !processedParagraph.isEmpty {
                processedParagraph = "<p>" + processedParagraph.replacingOccurrences(of: "\n", with: "<br/>") + "</p>"
            }

            html += processedParagraph
        }

        html += "</body></html>"
        return html
    }

    private func gmailFriendlyText(_ markdown: String) -> String {
        let normalized = markdown.replacingOccurrences(of: "\r\n", with: "\n")
        let lines = normalized.components(separatedBy: "\n")
        var outputLines: [String] = []
        var lastLineWasSpacer = false

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                if !outputLines.isEmpty, !lastLineWasSpacer {
                    outputLines.append("")
                    lastLineWasSpacer = true
                }
                continue
            }

            if trimmed == "---" {
                if !outputLines.isEmpty, !lastLineWasSpacer {
                    outputLines.append("")
                    lastLineWasSpacer = true
                }
                continue
            }

            let nextLine: String
            if trimmed.hasPrefix("### ") {
                nextLine = String(trimmed.dropFirst(4)) + ":"
            } else if trimmed.hasPrefix("## ") {
                nextLine = String(trimmed.dropFirst(3)) + ":"
            } else if trimmed.hasPrefix("# ") {
                nextLine = String(trimmed.dropFirst(2)) + ":"
            } else if trimmed.hasPrefix("- ") {
                nextLine = "- " + String(trimmed.dropFirst(2))
            } else if trimmed.hasPrefix("> ") {
                nextLine = "\"\(String(trimmed.dropFirst(2)))\""
            } else {
                nextLine = trimmed
            }

            outputLines.append(nextLine)
            lastLineWasSpacer = false
        }

        while outputLines.last == "" {
            outputLines.removeLast()
        }

        return outputLines.joined(separator: "\n")
    }
}
