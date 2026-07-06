//
//  MessageBubble.swift
//  newsly
//

import SwiftUI
import UIKit

struct MessageBubble: View {
    @Environment(\.openURL) private var openURL

    let message: ChatMessage
    let retryingCouncilChildSessionId: Int?
    var onDigDeeper: ((String) -> Void)?
    var onShare: ((String) -> Void)?
    var onRetryCouncilCandidate: ((CouncilCandidate) -> Void)?
    var feedOptionActionModel: AssistantFeedOptionActionModel

    var body: some View {
        Group {
            if message.isProcessSummary {
                ProcessSummaryRow(message: message)
            } else {
                standardMessageBubble
            }
        }
    }

    private var style: MessageBubbleStyle {
        MessageBubbleStyle(message: message)
    }

    private var assistantTextColor: UIColor {
        UIColor(Color.onSurface)
    }

    private var standardMessageBubble: some View {
        MessageBubbleChrome(style: style, timestamp: message.formattedTime) {
            bubbleContent
        }
        .contextMenu {
            if style.isAssistant {
                Button {
                    onShare?(message.content)
                } label: {
                    Label("Share", systemImage: "square.and.arrow.up")
                }
            }

            Button {
                UIPasteboard.general.string = message.content
            } label: {
                Label("Copy", systemImage: "doc.on.doc")
            }
        }
    }

    @ViewBuilder
    private var bubbleContent: some View {
        if message.isUser {
            Text(message.content)
                .font(.appCallout)
                .textSelection(.enabled)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                if message.hasCouncilCandidates {
                    CouncilCandidatesBubble(
                        message: message,
                        textColor: assistantTextColor,
                        retryingChildSessionId: retryingCouncilChildSessionId,
                        onRetryCandidate: { candidate in
                            onRetryCouncilCandidate?(candidate)
                        }
                    )
                } else {
                    SelectableMarkdownView(
                        markdown: message.content,
                        textColor: assistantTextColor,
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
}

struct ProcessSummaryRow: View {
    let message: ChatMessage
    @State private var isExpanded = false

    private var detail: String? {
        message.processSummaryDetail
    }

    var body: some View {
        VStack(alignment: .center, spacing: 6) {
            Button {
                guard detail != nil else { return }
                withAnimation(AppMotion.subtle) {
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "sparkles")
                        .font(.appCaption2)
                    Text(message.processSummaryText)
                        .lineLimit(isExpanded ? nil : 1)
                        .truncationMode(.tail)
                    if detail != nil {
                        Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                            .font(.appCaption2.weight(.semibold))
                    }
                }
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.surfaceContainer.opacity(0.8))
                .clipShape(Capsule())
            }
            .buttonStyle(.plain)

            if isExpanded, let detail {
                Text(detail)
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.leading)
                    .padding(.horizontal, 20)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity)
        .accessibilityLabel(message.processSummaryText)
    }
}

private struct MessageBubbleChrome<Content: View>: View {
    let style: MessageBubbleStyle
    let timestamp: String
    private let content: Content

    init(
        style: MessageBubbleStyle,
        timestamp: String,
        @ViewBuilder content: () -> Content
    ) {
        self.style = style
        self.timestamp = timestamp
        self.content = content()
    }

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            if style.isUser {
                Spacer(minLength: MessageBubbleMetrics.userLeadingClearance)
            }

            VStack(alignment: style.stackAlignment, spacing: 4) {
                bubbleSurface

                if !timestamp.isEmpty {
                    Text(timestamp)
                        .font(.appCaption2)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .padding(.horizontal, 4)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: style.frameAlignment)
        .foregroundStyle(style.foregroundColor)
    }

    @ViewBuilder
    private var bubbleSurface: some View {
        if style.rendersOwnBubble {
            framedContent
        } else {
            framedContent
                .padding(.horizontal, MessageBubbleMetrics.horizontalInset)
                .padding(.vertical, MessageBubbleMetrics.verticalInset)
                .background(style.backgroundColor, in: style.shape)
                .overlay {
                    if let strokeColor = style.strokeColor {
                        style.shape.stroke(strokeColor, lineWidth: 0.5)
                    }
                }
        }
    }

    @ViewBuilder
    private var framedContent: some View {
        if style.expandsContent {
            content.frame(maxWidth: .infinity, alignment: style.contentAlignment)
        } else {
            content
        }
    }
}

private struct MessageBubbleStyle {
    let isUser: Bool
    let rendersOwnBubble: Bool

    init(message: ChatMessage) {
        isUser = message.isUser
        rendersOwnBubble = !message.isUser && message.hasCouncilCandidates
    }

    var isAssistant: Bool {
        !isUser
    }

    var stackAlignment: HorizontalAlignment {
        isUser ? .trailing : .leading
    }

    var frameAlignment: Alignment {
        isUser ? .trailing : .leading
    }

    var contentAlignment: Alignment {
        isUser ? .trailing : .leading
    }

    var expandsContent: Bool {
        isAssistant
    }

    var foregroundColor: Color {
        isUser ? .chatUserBubbleText : .onSurface
    }

    var backgroundColor: Color {
        isUser ? Color.chatUserBubble.opacity(0.92) : Color.surfaceContainer.opacity(0.58)
    }

    var strokeColor: Color? {
        isAssistant ? Color.outlineVariant.opacity(0.12) : nil
    }

    var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
    }
}

private enum MessageBubbleMetrics {
    static let userLeadingClearance: CGFloat = 72
    static let horizontalInset: CGFloat = Spacing.rowHorizontal
    static let verticalInset: CGFloat = 8
}

#if DEBUG
#Preview("Message Bubble Stack") {
    VStack(spacing: 16) {
        MessageBubble(
            message: ChatPreviewFixtures.userMessage,
            retryingCouncilChildSessionId: nil,
            feedOptionActionModel: ChatPreviewActionModels.feedOptions()
        )
        MessageBubble(
            message: ChatPreviewFixtures.assistantMessage,
            retryingCouncilChildSessionId: nil,
            feedOptionActionModel: ChatPreviewActionModels.feedOptions()
        )
        MessageBubble(
            message: ChatPreviewFixtures.processSummaryMessage,
            retryingCouncilChildSessionId: nil,
            feedOptionActionModel: ChatPreviewActionModels.feedOptions()
        )
    }
    .padding()
    .background(Color.surfacePrimary)
}
#endif
