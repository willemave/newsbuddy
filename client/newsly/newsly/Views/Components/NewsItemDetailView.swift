//
//  NewsItemDetailView.swift
//  newsly
//
//  Created by Assistant on 9/23/25.
//

import SwiftUI

struct NewsItemDetailView: View {
    let content: ContentDetail
    private let keyPoints: [String]

    init(
        content: ContentDetail
    ) {
        self.content = content
        self.keyPoints = content.resolvedNewsKeyPoints.map(Self.plainKeyPointText)
    }

    var body: some View {
        if !keyPoints.isEmpty {
            keyPointsSection()
        }
    }

    @ViewBuilder
    private func keyPointsSection() -> some View {
        VStack(alignment: .leading, spacing: 16) {
            ReaderSectionHeader("Key Points")

            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(keyPoints.enumerated()), id: \.offset) { _, point in
                    Text(point)
                        .font(.appCallout)
                        .foregroundColor(Color.readerBodyText)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private static func plainKeyPointText(_ string: String) -> String {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        if let attributed = try? AttributedString(markdown: string, options: options) {
            return stripLeadingMarkdownBullets(from: String(attributed.characters))
        }
        return stripLeadingMarkdownBullets(from: string)
    }

    private static func stripLeadingMarkdownBullets(from string: String) -> String {
        string
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map { stripLeadingMarkdownBullet(from: String($0)) }
            .joined(separator: "\n")
    }

    private static func stripLeadingMarkdownBullet(from line: String) -> String {
        let whitespaceEnd = line.firstIndex { character in
            character != " " && character != "\t"
        } ?? line.endIndex
        let leadingWhitespace = line[..<whitespaceEnd]
        let remainder = line[whitespaceEnd...]

        for marker in ["* ", "- ", "+ "] where remainder.hasPrefix(marker) {
            return String(leadingWhitespace) + String(remainder.dropFirst(marker.count))
        }
        return line
    }
}
