//
//  ArticlePreviewCard.swift
//  newsly
//

import SwiftUI

struct ArticlePreviewCard: View {
    let title: String
    let source: String?
    let summary: String?
    let url: String?

    var body: some View {
        VStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 12) {
                Text(title)
                    .font(.headline)
                    .lineLimit(3)

                if let source {
                    HStack(spacing: 4) {
                        Image(systemName: "doc.text")
                            .font(.caption)
                        Text(source)
                            .font(.caption)
                    }
                    .foregroundStyle(Color.onSurfaceSecondary)
                }

                if let summary, !summary.isEmpty {
                    Text(summary)
                        .font(.subheadline)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(4)
                }

                if let urlString = url, let articleUrl = URL(string: urlString) {
                    Link(destination: articleUrl) {
                        HStack(spacing: 4) {
                            Text("Read original article")
                                .font(.caption)
                            Image(systemName: "arrow.up.right.square")
                                .font(.caption2)
                        }
                        .foregroundStyle(Color.topicAccent)
                    }
                }
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.surfaceSecondary)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

            VStack(spacing: 6) {
                Text("Ask me anything about this article")
                    .font(.subheadline)
                    .foregroundStyle(Color.onSurfaceSecondary)
                Text("I can summarize, explain, find related topics, or answer your questions.")
                    .font(.caption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
            }
        }
    }
}

#if DEBUG
#Preview("Article Preview Card") {
    ArticlePreviewCard(
        title: ChatPreviewFixtures.session.articleTitle ?? "Preview Article",
        source: ChatPreviewFixtures.session.articleSource,
        summary: ChatPreviewFixtures.session.articleSummary,
        url: ChatPreviewFixtures.session.articleUrl
    )
    .padding()
    .background(Color.surfacePrimary)
}
#endif
