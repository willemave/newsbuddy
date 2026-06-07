//
//  AssistantMessageBubble.swift
//  newsly
//

import SwiftUI
import UIKit

struct AssistantMessageBubble: View {
    let message: ChatMessage
    let retryingCouncilChildSessionId: Int?
    var onDigDeeper: ((String) -> Void)?
    var onShare: ((String) -> Void)?
    var onRetryCouncilCandidate: ((CouncilCandidate) -> Void)?
    var feedOptionActionModel: AssistantFeedOptionActionModel

    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if rendersOwnBubble {
                messageContent
            } else {
                messageContent
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(Color.surfaceContainer.opacity(0.58))
                    .clipShape(bubbleShape)
                    .overlay(
                        bubbleShape
                            .stroke(Color.outlineVariant.opacity(0.12), lineWidth: 0.5)
                    )
            }

            if !message.formattedTime.isEmpty {
                Text(message.formattedTime)
                    .font(.appCaption2)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .padding(.horizontal, 4)
            }
        }
        .contextMenu {
            Button {
                onShare?(message.content)
            } label: {
                Label("Share", systemImage: "square.and.arrow.up")
            }

            Button {
                UIPasteboard.general.string = message.content
            } label: {
                Label("Copy", systemImage: "doc.on.doc")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var rendersOwnBubble: Bool {
        message.hasCouncilCandidates
    }

    private var textColor: UIColor {
        UIColor(Color.onSurface)
    }

    private var bubbleShape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(
            topLeadingRadius: 12,
            bottomLeadingRadius: 12,
            bottomTrailingRadius: 12,
            topTrailingRadius: 12
        )
    }

    private var messageContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            if message.hasCouncilCandidates {
                CouncilCandidatesBubble(
                    message: message,
                    textColor: textColor,
                    retryingChildSessionId: retryingCouncilChildSessionId,
                    onRetryCandidate: { candidate in
                        onRetryCouncilCandidate?(candidate)
                    }
                )
            } else {
                SelectableMarkdownView(
                    markdown: message.content,
                    textColor: textColor,
                    baseFont: .appSans(textStyle: .callout),
                    onDigDeeper: onDigDeeper
                )
            }

            if message.hasFeedOptions {
                AssistantFeedOptionsSection(
                    options: message.feedOptions,
                    actionModel: feedOptionActionModel,
                    onPreview: { option in
                        guard let url = URL(string: option.previewURLString) else { return }
                        openURL(url)
                    }
                )
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#if DEBUG
#Preview("Assistant Message Bubble") {
    AssistantMessageBubble(
        message: ChatPreviewFixtures.assistantMessage,
        retryingCouncilChildSessionId: nil,
        onDigDeeper: { _ in },
        onShare: { _ in },
        feedOptionActionModel: ChatPreviewActionModels.feedOptions()
    )
    .padding()
    .background(Color.surfacePrimary)
}

#Preview("Assistant Feed Options") {
    AssistantMessageBubble(
        message: ChatPreviewFixtures.assistantWithFeedOptions,
        retryingCouncilChildSessionId: nil,
        onDigDeeper: { _ in },
        onShare: { _ in },
        feedOptionActionModel: ChatPreviewActionModels.feedOptions()
    )
    .padding()
    .background(Color.surfacePrimary)
}
#endif
