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
                    .font(.appHeadline)
                    .lineLimit(3)

                if let source, !source.isEmpty {
                    HStack(spacing: 4) {
                        Image(systemName: "doc.text")
                            .font(.appCaption)
                        Text(source)
                            .font(.appCaption)
                    }
                    .foregroundStyle(Color.onSurfaceSecondary)
                }

                if let summary, !summary.isEmpty {
                    Text(summary)
                        .font(.appSubheadline)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(4)
                }

                if let urlString = url, let articleUrl = URL(string: urlString) {
                    Link(destination: articleUrl) {
                        HStack(spacing: 4) {
                            Text("Read original article")
                                .font(.appCaption)
                            Image(systemName: "arrow.up.right.square")
                                .font(.appCaption2)
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
                    .font(.appSubheadline)
                    .foregroundStyle(Color.onSurfaceSecondary)
                Text("I can summarize, explain, find related topics, or answer your questions.")
                    .font(.appCaption)
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
