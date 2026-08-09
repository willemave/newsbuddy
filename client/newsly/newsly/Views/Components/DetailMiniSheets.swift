//
//  DetailMiniSheets.swift
//  newsly
//

import SwiftUI

struct DetailShareSheet: View {
    let onClose: () -> Void
    let onQueueShare: (ShareContentOption) -> Void
    let onOpenTweetSuggestions: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            MiniSheetHeader(
                title: "Share",
                titleAccessibilityIdentifier: "content.share.sheet",
                dismiss: onClose
            )

            VStack(spacing: 8) {
                MiniSheetOptionRow(
                    icon: "link",
                    title: "Title + link",
                    subtitle: "Headline and URL only",
                    accessibilityIdentifier: "content.share.title_link",
                    action: { onQueueShare(.light) }
                )
                MiniSheetOptionRow(
                    icon: "text.quote",
                    title: "Key points",
                    subtitle: "Summary, top quotes, and link",
                    accessibilityIdentifier: "content.share.key_points",
                    action: { onQueueShare(.medium) }
                )
                MiniSheetOptionRow(
                    icon: "doc.plaintext",
                    title: "Full content",
                    subtitle: "Complete article or transcript",
                    accessibilityIdentifier: "content.share.full_content",
                    action: { onQueueShare(.full) }
                )
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)

            Divider()
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.vertical, 12)

            MiniSheetOptionRow(
                icon: "at",
                title: "Tweet suggestions",
                subtitle: "Generate tweet-ready snippets",
                accessibilityIdentifier: "content.share.tweet_suggestions",
                action: onOpenTweetSuggestions
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
    }
}

struct DetailDownloadSheet: View {
    let onClose: () -> Void
    let onDownload: (Int) -> Void

    var body: some View {
        VStack(spacing: 0) {
            MiniSheetHeader(
                title: "Load more from series",
                titleAccessibilityIdentifier: "content.download.sheet",
                dismiss: onClose
            )

            VStack(spacing: 8) {
                MiniSheetOptionRow(
                    icon: "square.stack",
                    iconColor: .readerBodyText,
                    title: "3 episodes",
                    subtitle: "Quick catch-up",
                    accessibilityIdentifier: "content.download.3",
                    action: { onDownload(3) }
                )
                MiniSheetOptionRow(
                    icon: "square.stack",
                    iconColor: .readerBodyText,
                    title: "5 episodes",
                    subtitle: "Recent backlog",
                    accessibilityIdentifier: "content.download.5",
                    action: { onDownload(5) }
                )
                MiniSheetOptionRow(
                    icon: "square.stack.3d.up",
                    iconColor: .readerBodyText,
                    title: "10 episodes",
                    subtitle: "Deep dive into the series",
                    accessibilityIdentifier: "content.download.10",
                    action: { onDownload(10) }
                )
                MiniSheetOptionRow(
                    icon: "square.stack.3d.up.fill",
                    iconColor: .readerBodyText,
                    title: "20 episodes",
                    subtitle: "Full archive pull",
                    accessibilityIdentifier: "content.download.20",
                    action: { onDownload(20) }
                )
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
    }
}
