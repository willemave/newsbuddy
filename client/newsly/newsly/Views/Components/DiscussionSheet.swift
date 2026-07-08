//
//  DiscussionSheet.swift
//  newsly
//

import SwiftUI

enum DiscussionTab: String, CaseIterable {
    case comments = "COMMENTS"
    case links = "LINKS"
}

struct DiscussionSheet: View {
    let isLoading: Bool
    let discussion: ContentDiscussion?
    let unavailableText: String
    let fallbackURL: URL?
    let canRetry: Bool
    @Binding var selectedTab: DiscussionTab
    @Binding var collapsedCommentIDs: Set<String>
    let addStateForLink: (String) -> DiscussionLinkAddState
    let onClose: () -> Void
    let onOpenURL: (URL) -> Void
    let onRetry: () -> Void
    let onAddLink: (DiscussionLink) -> Void

    static func presentationDetents(
        isLoading: Bool,
        discussion: ContentDiscussion?
    ) -> Set<PresentationDetent> {
        guard !isLoading,
              let discussion,
              discussion.hasRenderableContent else {
            return [.height(250)]
        }
        if usesCompactDetent(discussion) {
            return [.height(compactDetentHeight(for: discussion))]
        }
        return [.medium, .large]
    }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    loadingView
                } else if let discussion, discussion.hasRenderableContent {
                    discussionContent(discussion)
                } else {
                    unavailableView
                }
            }
            .navigationTitle("Discussion")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done", action: onClose)
                }
            }
        }
        .accessibilityIdentifier("content.discussion.sheet")
    }

    @ViewBuilder
    private func discussionContent(_ discussion: ContentDiscussion) -> some View {
        if discussion.mode == "discussion_list" {
            groupedLinksContent(discussion)
        } else if discussion.summary != nil {
            summaryDiscussionContent(discussion)
        } else {
            tabbedDiscussionContent(discussion)
        }
    }

    private func groupedLinksContent(_ discussion: ContentDiscussion) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if discussion.discussionGroups.isEmpty {
                    Text("No discussion links available.")
                        .font(.appSubheadline)
                        .foregroundColor(Color.onSurfaceSecondary)
                } else {
                    ForEach(discussion.discussionGroups) { group in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(group.label)
                                .font(.appHeadline)
                            ForEach(group.items) { item in
                                if let url = URL(string: item.url) {
                                    Button {
                                        onOpenURL(url)
                                    } label: {
                                        HStack(spacing: 8) {
                                            Image(systemName: "arrow.up.right.square")
                                            Text(item.title)
                                                .multilineTextAlignment(.leading)
                                        }
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                        .padding(.bottom, 4)
                    }
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 16)
        }
    }

    private func summaryDiscussionContent(_ discussion: ContentDiscussion) -> some View {
        ScrollView {
            let commentIndex = DiscussionCommentIndexer.build(from: discussion.comments)
            let linksOutsideSummary = discussion.linksOutsideSummary
            VStack(alignment: .leading, spacing: 0) {
                discussionSummaryContent(discussion)
                if !linksOutsideSummary.isEmpty {
                    linksTabContent(
                        links: linksOutsideSummary,
                        commentsByID: commentIndex.commentsByID
                    )
                }
                if !discussion.comments.isEmpty {
                    commentsTabContent(commentIndex: commentIndex)
                }
            }
        }
    }

    private func tabbedDiscussionContent(_ discussion: ContentDiscussion) -> some View {
        VStack(spacing: 0) {
            if !discussion.links.isEmpty {
                Picker("Tab", selection: $selectedTab) {
                    ForEach(DiscussionTab.allCases, id: \.self) { tab in
                        Text(tab.rawValue).tag(tab)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.vertical, 10)
            }

            ScrollView {
                let commentIndex = DiscussionCommentIndexer.build(from: discussion.comments)
                switch selectedTab {
                case .comments:
                    commentsTabContent(commentIndex: commentIndex)
                case .links:
                    linksTabContent(
                        links: discussion.links,
                        commentsByID: commentIndex.commentsByID
                    )
                }
            }
        }
    }

    private var loadingView: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.large)
            Text("Loading discussion...")
                .font(.appSubheadline)
                .foregroundColor(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(24)
    }

    private var unavailableView: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Discussion unavailable")
                    .font(.appHeadline)

                Text(unavailableText)
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let fallbackURL {
                Button {
                    onOpenURL(fallbackURL)
                } label: {
                    Label("Open original discussion", systemImage: "arrow.up.right.square")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }

            if canRetry {
                Button("Try again", action: onRetry)
                    .buttonStyle(.bordered)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .padding(20)
    }

    @ViewBuilder
    private func discussionSummaryContent(_ discussion: ContentDiscussion) -> some View {
        if let summary = discussion.summary {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 8) {
                    sectionHeaderText("Community Summary")

                    Text(summary.overview)
                        .font(.appCallout)
                        .foregroundColor(Color.readerBodyText)
                        .fixedSize(horizontal: false, vertical: true)

                    if let url = discussionSummaryURL(summary: summary, discussion: discussion) {
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

                if !summary.topics.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        sectionHeaderText("Key Topics")

                        ForEach(summary.topics) { topic in
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

                if !summary.representativeComments.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        sectionHeaderText("Representative Comments")

                        ForEach(summary.representativeComments) { comment in
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

                if !summary.notableLinks.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        sectionHeaderText("Notable Links")

                        ForEach(summary.notableLinks) { link in
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
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 16)
        }
    }

    private func commentsTabContent(commentIndex: DiscussionCommentIndex) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if commentIndex.orderedComments.isEmpty {
                Text("No comments available.")
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .padding(.top, 20)
                    .frame(maxWidth: .infinity)
            } else {
                ForEach(commentIndex.orderedComments) { comment in
                    if !DiscussionCommentIndexer.isHiddenByCollapse(
                        comment,
                        collapsedCommentIDs: collapsedCommentIDs,
                        commentsByID: commentIndex.commentsByID
                    ) {
                        let indent = CGFloat(min(comment.depth, 5)) * 16
                        let isCollapsed = collapsedCommentIDs.contains(comment.commentID)
                        let childCount = commentIndex.descendantCountByID[comment.commentID] ?? 0

                        VStack(alignment: .leading, spacing: 6) {
                            if !isCollapsed {
                                Text(comment.compactText ?? comment.text)
                                    .font(.appCallout)
                                    .fontWeight(.regular)
                                    .foregroundColor(Color.readerBodyText)
                                    .fixedSize(horizontal: false, vertical: true)
                            } else if childCount > 0 {
                                HStack(spacing: 6) {
                                    Text("+\(childCount)")
                                        .font(.appCaption2)
                                        .fontWeight(.semibold)
                                        .foregroundColor(.terracottaPrimary)
                                        .padding(.horizontal, 5)
                                        .padding(.vertical, 1)
                                        .background(Color.terracottaPrimary.opacity(0.12))
                                        .clipShape(Capsule())

                                    Image(systemName: "chevron.right")
                                        .font(.appCaption2)
                                        .foregroundColor(Color.onSurfaceSecondary.opacity(0.6))
                                }
                            }
                        }
                        .padding(12)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .overlay(alignment: .leading) {
                            if comment.depth > 0 {
                                RoundedRectangle(cornerRadius: 1.5)
                                    .fill(Color.terracottaPrimary)
                                    .frame(width: 3)
                                    .padding(.vertical, 4)
                            }
                        }
                        .padding(.leading, indent)
                        .accessibilityIdentifier("content.discussion.comment.\(comment.commentID)")
                        .contentShape(Rectangle())
                        .onTapGesture {
                            guard childCount > 0 else { return }
                            withAnimation(AppMotion.subtle) {
                                if isCollapsed {
                                    collapsedCommentIDs.remove(comment.commentID)
                                } else {
                                    collapsedCommentIDs.insert(comment.commentID)
                                }
                            }
                        }
                    }
                }
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 16)
    }

    private func linksTabContent(
        links: [DiscussionLink],
        commentsByID: [String: DiscussionComment]
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if links.isEmpty {
                Text("No links found.")
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .padding(.top, 20)
                    .frame(maxWidth: .infinity)
            } else {
                ForEach(links) { link in
                    if let url = URL(string: link.url) {
                        let addState = addStateForLink(link.id)

                        VStack(alignment: .leading, spacing: 10) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(link.title ?? link.url)
                                    .font(.appCallout)
                                    .fontWeight(.medium)
                                    .foregroundColor(Color.onSurface)
                                    .multilineTextAlignment(.leading)
                                    .lineLimit(2)

                                Text(link.url)
                                    .font(.appCaption2)
                                    .foregroundColor(Color.onSurfaceSecondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)

                                if let commentID = link.commentID,
                                   let comment = commentsByID[commentID] {
                                    Text(comment.compactText ?? String(comment.text.prefix(120)))
                                        .font(.appCaption)
                                        .foregroundColor(Color.onSurfaceSecondary)
                                        .lineLimit(2)
                                        .padding(.top, 2)
                                }

                                HStack(spacing: 4) {
                                    Image(systemName: "arrow.up.right")
                                        .font(.appCaption2)
                                    Text(link.source)
                                        .font(.appCaption2)
                                }
                                .foregroundColor(.onSurfaceSecondary)
                            }

                            HStack(spacing: 10) {
                                Button {
                                    onOpenURL(url)
                                } label: {
                                    Label("Open", systemImage: "arrow.up.right.square")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.bordered)

                                Button {
                                    onAddLink(link)
                                } label: {
                                    HStack(spacing: 6) {
                                        if addState == .adding {
                                            ProgressView()
                                                .controlSize(.small)
                                        } else {
                                            Image(systemName: discussionLinkAddIcon(for: addState))
                                        }
                                        Text(discussionLinkAddTitle(for: addState))
                                    }
                                    .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(Color.terracottaPrimary)
                                .disabled(isLinkActionDisabled(addState))
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
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 16)
    }

    private func discussionSummaryURL(
        summary: DiscussionSummary,
        discussion: ContentDiscussion
    ) -> URL? {
        let rawURL = summary.externalDiscussionURL ?? discussion.discussionURL ?? discussion.sourceURL
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }

    private static func usesCompactDetent(_ discussion: ContentDiscussion) -> Bool {
        discussion.mode == "comments"
            && discussion.summary == nil
            && discussion.links.isEmpty
            && discussion.comments.count <= 2
    }

    private static func compactDetentHeight(for discussion: ContentDiscussion) -> CGFloat {
        discussion.comments.count <= 1 ? 250 : 320
    }

    private func sectionHeaderText(_ title: String) -> some View {
        Text(title.uppercased())
            .font(.readerBody.weight(.bold))
            .foregroundColor(Color.readerBodyText)
            .tracking(0.4)
    }

    private func isLinkActionDisabled(_ state: DiscussionLinkAddState) -> Bool {
        state == .adding || state == .added
    }

    private func discussionLinkAddTitle(for state: DiscussionLinkAddState) -> String {
        switch state {
        case .idle:
            return "Add to Long Form"
        case .adding:
            return "Adding"
        case .added:
            return "Added"
        case .failed:
            return "Retry"
        }
    }

    private func discussionLinkAddIcon(for state: DiscussionLinkAddState) -> String {
        switch state {
        case .idle:
            return "plus"
        case .adding:
            return "plus"
        case .added:
            return "checkmark"
        case .failed:
            return "arrow.clockwise"
        }
    }
}

struct CommunityDiscussionSummarySection: View {
    let discussion: ContentDiscussion
    let onOpenComments: (URL) -> Void
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
                HStack(alignment: .center, spacing: 4) {
                    Button {
                        onOpenComments(url)
                    } label: {
                        headerIcon("bubble.left.and.bubble.right")
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("View full discussion")
                    .accessibilityIdentifier("content.discussion.open")

                    Button {
                        onOpenURL(url)
                    } label: {
                        headerIcon("arrow.up.right.square")
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Open original discussion")
                }
                .fixedSize(horizontal: true, vertical: false)
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
    }

    private func headerIcon(_ systemName: String) -> some View {
        Image(systemName: systemName)
            .font(.appSymbol(size: 17, weight: .regular))
            .foregroundColor(Color.onSurfaceSecondary)
            .frame(width: 40, height: 40)
            .contentShape(Rectangle())
    }

    private func discussionSummaryURL(summary: DiscussionSummary) -> URL? {
        let rawURL = summary.externalDiscussionURL ?? discussion.discussionURL ?? discussion.sourceURL
        guard let rawURL else { return nil }
        return URL(string: rawURL)
    }
}
