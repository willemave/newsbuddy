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
            MiniSheetHeader(title: "Share", dismiss: onClose)

            VStack(spacing: 8) {
                MiniSheetOptionRow(
                    icon: "link",
                    title: "Title + link",
                    subtitle: "Headline and URL only",
                    action: { onQueueShare(.light) }
                )
                MiniSheetOptionRow(
                    icon: "text.quote",
                    title: "Key points",
                    subtitle: "Summary, top quotes, and link",
                    action: { onQueueShare(.medium) }
                )
                MiniSheetOptionRow(
                    icon: "doc.plaintext",
                    title: "Full content",
                    subtitle: "Complete article or transcript",
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
                action: onOpenTweetSuggestions
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("content.share.sheet")
    }
}

struct DetailDownloadSheet: View {
    let onClose: () -> Void
    let onDownload: (Int) -> Void

    var body: some View {
        VStack(spacing: 0) {
            MiniSheetHeader(title: "Load more from series", dismiss: onClose)

            VStack(spacing: 8) {
                MiniSheetOptionRow(
                    icon: "square.stack",
                    iconColor: .readerBodyText,
                    title: "3 episodes",
                    subtitle: "Quick catch-up",
                    action: { onDownload(3) }
                )
                MiniSheetOptionRow(
                    icon: "square.stack",
                    iconColor: .readerBodyText,
                    title: "5 episodes",
                    subtitle: "Recent backlog",
                    action: { onDownload(5) }
                )
                MiniSheetOptionRow(
                    icon: "square.stack.3d.up",
                    iconColor: .readerBodyText,
                    title: "10 episodes",
                    subtitle: "Deep dive into the series",
                    action: { onDownload(10) }
                )
                MiniSheetOptionRow(
                    icon: "square.stack.3d.up.fill",
                    iconColor: .readerBodyText,
                    title: "20 episodes",
                    subtitle: "Full archive pull",
                    action: { onDownload(20) }
                )
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.surfacePrimary)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("content.download.sheet")
    }
}
