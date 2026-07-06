import SwiftUI

struct DiscussionSummaryView: View {
    let discussion: ContentDiscussion
    let onOpenURL: (URL) -> Void

    var body: some View {
        if let summary = discussion.summary {
            VStack(alignment: .leading, spacing: 18) {
                summarySection(summary)
                topicsSection(summary.topics)
                representativeCommentsSection(summary.representativeComments)
                notableLinksSection(summary.notableLinks)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 16)
        }
    }

    private func summarySection(_ summary: DiscussionSummary) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("Community Summary")

            Text(summary.overview)
                .font(.appCallout)
                .foregroundColor(Color.readerBodyText)
                .fixedSize(horizontal: false, vertical: true)

            if let url = summaryURL(summary: summary) {
                Button {
                    onOpenURL(url)
                } label: {
                    Label("Open original discussion", systemImage: "arrow.up.right.square")
                }
                .buttonStyle(.plain)
                .font(.appSubheadline)
                .padding(.top, 4)
            }
        }
    }

    @ViewBuilder
    private func topicsSection(_ topics: [DiscussionSummaryTopic]) -> some View {
        if !topics.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                sectionHeader("Key Topics")

                ForEach(topics) { topic in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(topic.title.uppercased())
                            .font(.appCallout.weight(.bold))
                            .foregroundColor(Color.readerBodyText)
                            .tracking(0.4)
                        Text(topic.summary)
                            .font(.appSubheadline)
                            .foregroundColor(Color.readerBodyText)
                            .fixedSize(horizontal: false, vertical: true)
                        if let stance = topic.stance {
                            Text(stance)
                                .font(.appCaption)
                                .foregroundColor(Color.onSurfaceSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.surfaceSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }

    @ViewBuilder
    private func representativeCommentsSection(_ comments: [DiscussionSummaryComment]) -> some View {
        if !comments.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                sectionHeader("Representative Comments")

                ForEach(comments) { comment in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(comment.author ?? "unknown")
                            .font(.appCaption)
                            .fontWeight(.medium)
                            .foregroundColor(Color.onSurfaceSecondary)
                        Text(comment.text)
                            .font(.appSubheadline)
                            .foregroundColor(Color.readerBodyText)
                            .fixedSize(horizontal: false, vertical: true)
                        if let reason = comment.reason {
                            Text(reason)
                                .font(.appCaption)
                                .foregroundColor(Color.onSurfaceSecondary)
                        }
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.surfaceSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
        }
    }

    @ViewBuilder
    private func notableLinksSection(_ links: [DiscussionSummaryLink]) -> some View {
        if !links.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                sectionHeader("Notable Links")

                ForEach(links) { link in
                    if let url = URL(string: link.url) {
                        Button {
                            onOpenURL(url)
                        } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                HStack(spacing: 6) {
                                    Image(systemName: "arrow.up.right.square")
                                    Text(link.title ?? link.url)
                                        .fontWeight(.medium)
                                        .multilineTextAlignment(.leading)
                                }
                                .font(.appSubheadline)

                                if let reason = link.reason {
                                    Text(reason)
                                        .font(.appCaption)
                                        .foregroundColor(Color.onSurfaceSecondary)
                                        .multilineTextAlignment(.leading)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                        .padding(12)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                }
            }
        }
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .font(.readerBody.weight(.bold))
            .foregroundColor(Color.readerBodyText)
            .tracking(0.4)
    }

    private func summaryURL(summary: DiscussionSummary) -> URL? {
        let rawURL = summary.externalDiscussionURL ?? discussion.discussionURL ?? discussion.sourceURL
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }
}
