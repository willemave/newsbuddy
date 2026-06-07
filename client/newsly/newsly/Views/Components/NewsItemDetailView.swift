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
                    .font(.appHeadline)
                    .fontWeight(.semibold)

                Spacer()

                if let url = discussionURL {
                    Button(action: {
                        onDiscussionTap?(url)
                    }) {
                        Label("Comments", systemImage: "bubble.left.and.bubble.right")
                            .font(.appCaption)
                            .fontWeight(.medium)
                            .foregroundColor(.onSurfaceSecondary)
                            .padding(.horizontal, 10)
                            .frame(minHeight: 44)
                            .background(Color.surfaceTertiary)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .contentShape(Rectangle())
                    .accessibilityLabel("Comments")
                    .accessibilityIdentifier("content.discussion.open")
                }
            }

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

    private func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

}
