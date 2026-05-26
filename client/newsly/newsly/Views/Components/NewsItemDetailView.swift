//
//  NewsItemDetailView.swift
//  newsly
//
//  Created by Assistant on 9/23/25.
//

import SwiftUI

private enum NewsItemDetailDesign {
    static let bulletMarkerWidth: CGFloat = 12
    static let bulletTextSpacing: CGFloat = 8
}

struct NewsItemDetailView: View {
    let content: ContentDetail
    let metadata: NewsMetadata
    let onDiscussionTap: ((URL) -> Void)?

    var body: some View {
        if !keyPoints.isEmpty {
            keyPointsSection()
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
                            .foregroundColor(.brandSecondary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 5)
                            .background(Color.brandSecondary.opacity(0.12))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("content.discussion.open")
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(keyPoints.enumerated()), id: \.offset) { _, point in
                    HStack(alignment: .firstTextBaseline, spacing: NewsItemDetailDesign.bulletTextSpacing) {
                        Text(verbatim: "\u{2022}")
                            .font(.callout.weight(.semibold))
                            .foregroundColor(Color.brandPrimary.opacity(0.85))
                            .frame(width: NewsItemDetailDesign.bulletMarkerWidth, alignment: .center)

                        Text(point)
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

}
