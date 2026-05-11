//
//  NewsItemDetailView.swift
//  newsly
//
//  Created by Assistant on 9/23/25.
//

import SwiftUI

struct NewsItemDetailView: View {
    let content: ContentDetail
    let metadata: NewsMetadata
    let onDiscussionTap: ((URL) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
            if !keyPoints.isEmpty {
                keyPointsSection()
            }

            if hasArticleSection {
                articleLink()
            }
        }
    }

    private var keyPoints: [String] {
        content.resolvedNewsKeyPoints
    }

    private var discussionURL: URL? {
        let rawURL = normalizedText(content.newsDiscussionURL) ?? normalizedText(metadata.discussionURL)
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }

    private var articleTitle: String? {
        guard let title = normalizedText(metadata.article?.title) else { return nil }
        if isDuplicate(title, comparedTo: content.displayTitle)
            || isDuplicate(title, comparedTo: content.resolvedNewsSummaryText) {
            return nil
        }
        return title
    }

    private var articleURL: URL? {
        let rawURL = normalizedText(metadata.article?.url) ?? normalizedText(content.newsArticleURL)
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }

    private var hasArticleSection: Bool {
        articleTitle != nil || articleURL != nil
    }


    @ViewBuilder
    private func keyPointsSection() -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Key Points")
                    .font(.headline)
                    .fontWeight(.semibold)

                Spacer()

                if let url = discussionURL {
                    Button(action: {
                        onDiscussionTap?(url)
                    }) {
                        Label("Comments", systemImage: "bubble.left.and.bubble.right")
                            .font(.caption)
                            .fontWeight(.medium)
                            .foregroundColor(.orange)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(Color.orange.opacity(0.1))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("content.discussion.open")
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(keyPoints.enumerated()), id: \.offset) { _, point in
                    HStack(alignment: .top, spacing: 12) {
                        Circle()
                            .fill(Color.accentColor.opacity(0.85))
                            .frame(width: 6, height: 6)
                            .padding(.top, 7)

                        Text(point)
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func articleLink() -> some View {
        if let title = articleTitle, let url = articleURL {
            Link(destination: url) {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.up.right.square")
                        .font(.callout)
                    Text(title)
                        .font(.callout)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else if let url = articleURL {
            Link(destination: url) {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.up.right.square")
                        .font(.callout)
                    Text(url.absoluteString)
                        .font(.callout)
                        .lineLimit(2)
                }
            }
        }
    }

    private func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func normalizedURLKey(_ value: String?) -> String? {
        normalizedText(value)?.lowercased()
    }

    private func normalizedComparisonKey(_ value: String?) -> String? {
        guard let value = normalizedText(value)?.lowercased() else { return nil }
        let transformed = value.unicodeScalars.map { scalar -> String in
            CharacterSet.alphanumerics.contains(scalar) ? String(scalar) : " "
        }
        let collapsed = transformed.joined()
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
        return collapsed.isEmpty ? nil : collapsed
    }

    private func isDuplicate(_ lhs: String?, comparedTo rhs: String?) -> Bool {
        guard let left = normalizedComparisonKey(lhs), let right = normalizedComparisonKey(rhs) else {
            return false
        }
        return left == right || left.contains(right) || right.contains(left)
    }

}
