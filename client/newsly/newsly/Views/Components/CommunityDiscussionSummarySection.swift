//
//  CommunityDiscussionSummarySection.swift
//  newsly
//

import SwiftUI

struct CommunityDiscussionSummarySection: View {
    let discussion: ContentDiscussion
    let onOpenURL: (URL) -> Void

    var body: some View {
        if let summary = discussion.summary {
            VStack(alignment: .leading, spacing: 14) {
                summaryHeader(summary: summary)

                if !summary.topics.isEmpty {
                    VStack(alignment: .leading, spacing: 16) {
                        ForEach(Array(summary.topics.prefix(4))) { topic in
                            topicRow(topic)
                        }
                    }
                }
            }
        }
    }

    private func summaryHeader(summary: DiscussionSummary) -> some View {
        ReaderSectionHeader("Comments") {
            Spacer(minLength: 10)

            if let url = discussionSummaryURL(summary: summary) {
                Button {
                    onOpenURL(url)
                } label: {
                    Image(systemName: "arrow.up.right.square")
                        .font(.appSymbol(size: 17, weight: .regular))
                        .foregroundColor(Color.onSurfaceSecondary)
                        .frame(width: 40, height: 40)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Open original discussion")
                .accessibilityIdentifier("content.comments.open_original")
            }
        }
    }

    private func topicRow(_ topic: DiscussionSummaryTopic) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            if let stance = topic.stance {
                Text(stance)
                    .font(.appFootnote.weight(.semibold))
                    .foregroundColor(Color.onSurfaceSecondary)
                    .textCase(.uppercase)
                    .tracking(0.6)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            Text(topic.summary)
                .font(.appCallout)
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("content.comments.topic")
    }

    private func discussionSummaryURL(summary: DiscussionSummary) -> URL? {
        let rawURL = summary.externalDiscussionURL ?? discussion.discussionURL ?? discussion.sourceURL
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }
}
