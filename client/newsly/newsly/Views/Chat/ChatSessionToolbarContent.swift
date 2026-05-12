//
//  ChatSessionToolbarContent.swift
//  newsly
//

import SwiftUI

struct ChatSessionToolbarContent: ToolbarContent {
    let session: ChatSessionSummary?
    let onOpenArticle: (String) -> Void

    var body: some ToolbarContent {
        if let session {
            ToolbarItem(placement: .principal) {
                titleContent(for: session)
            }
        }
    }

    @ViewBuilder
    private func titleContent(for session: ChatSessionSummary) -> some View {
        if let articleUrl = session.articleUrl {
            Button {
                onOpenArticle(articleUrl)
            } label: {
                HStack(spacing: 4) {
                    titleText(for: session)
                    Image(systemName: "arrow.up.right.square")
                        .font(.caption2)
                }
                .foregroundStyle(Color.onSurface)
            }
        } else {
            titleText(for: session)
        }
    }

    private func titleText(for session: ChatSessionSummary) -> some View {
        Text(session.displayTitle)
            .font(.subheadline)
            .fontWeight(.semibold)
            .lineLimit(1)
            .truncationMode(.tail)
    }
}
