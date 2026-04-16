//
//  MessageBubble.swift
//  newsly
//

import SwiftUI

struct MessageBubble: View {
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
            } else if message.isUser {
                UserMessageBubble(message: message)
            } else {
                AssistantMessageBubble(
                    message: message,
                    retryingCouncilChildSessionId: retryingCouncilChildSessionId,
                    onDigDeeper: onDigDeeper,
                    onShare: onShare,
                    onRetryCouncilCandidate: onRetryCouncilCandidate,
                    feedOptionActionModel: feedOptionActionModel
                )
            }
        }
    }
}

struct ProcessSummaryRow: View {
    let message: ChatMessage
    @State private var isExpanded = false

    var body: some View {
        VStack(alignment: .center, spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) {
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "sparkles")
                        .font(.caption2)
                    Text(message.processSummaryText)
                        .lineLimit(isExpanded ? nil : 1)
                        .truncationMode(.tail)
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption2.weight(.semibold))
                }
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.surfaceContainer.opacity(0.8))
                .clipShape(Capsule())
            }
            .buttonStyle(.plain)

            if isExpanded, !message.content.isEmpty, message.content != message.processSummaryText {
                Text(message.content)
                    .font(.caption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 20)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity)
        .accessibilityLabel(message.processSummaryText)
    }
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
