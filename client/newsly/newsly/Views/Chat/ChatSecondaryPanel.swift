//
//  ChatSecondaryPanel.swift
//  newsly
//

import SwiftUI

struct ChatSecondaryPanel: View {
    let session: ChatSessionSummary?
    let activeCouncilCandidate: CouncilCandidate?
    let onOpenArticle: (String) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Context")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.onSurface)

                if let session {
                    VStack(alignment: .leading, spacing: 12) {
                        if let articleTitle = session.articleTitle {
                            Text(articleTitle)
                                .font(.headline)
                                .foregroundStyle(Color.onSurface)
                        } else {
                            Text(session.displayTitle)
                                .font(.headline)
                                .foregroundStyle(Color.onSurface)
                        }

                        if let source = session.articleSource {
                            Text(source)
                                .font(.caption.weight(.medium))
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }

                        if let summary = session.articleSummary, !summary.isEmpty {
                            Text(summary)
                                .font(.subheadline)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }

                        if let articleUrl = session.articleUrl {
                            Button {
                                onOpenArticle(articleUrl)
                            } label: {
                                Label("Open article", systemImage: "arrow.up.right.square")
                                    .font(.terracottaBodySmall)
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(Color.chatAccent)
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.surfaceSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                }

                if let activeCouncilCandidate {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Active Council Branch")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Color.chatAccent)

                        Text(activeCouncilCandidate.personaName)
                            .font(.headline)
                            .foregroundStyle(Color.onSurface)

                        Text(activeCouncilCandidate.content)
                            .font(.subheadline)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(12)
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.surfaceSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Color.surfacePrimary)
    }
}

#if DEBUG
#Preview("Chat Secondary Panel") {
    ChatSecondaryPanel(
        session: ChatPreviewFixtures.session,
        activeCouncilCandidate: ChatPreviewFixtures.councilCandidates.first,
        onOpenArticle: { _ in }
    )
}
#endif
