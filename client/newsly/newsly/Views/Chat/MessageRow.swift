//
//  MessageRow.swift
//  newsly
//

import SwiftUI

struct MessageRow: View {
    let item: ChatTimelineItem
    let retryingCouncilChildSessionId: Int?
    let feedOptionActionModel: AssistantFeedOptionActionModel
    let onRetrySend: (String) -> Void
    let onRetryCouncilCandidate: (CouncilCandidate) -> Void
    let onDigDeeper: (String) -> Void
    let onShare: (String) -> Void

    var body: some View {
        VStack(alignment: item.message.isUser ? .trailing : .leading, spacing: 8) {
            MessageBubble(
                message: item.message,
                retryingCouncilChildSessionId: retryingCouncilChildSessionId,
                onDigDeeper: onDigDeeper,
                onShare: onShare,
                onRetryCouncilCandidate: onRetryCouncilCandidate,
                feedOptionActionModel: feedOptionActionModel
            )

            if item.message.hasFailed, let retryText = item.retryText {
                VStack(alignment: .trailing, spacing: 6) {
                    if let error = item.message.error, !error.isEmpty {
                        Text(error)
                            .font(.terracottaBodySmall)
                            .foregroundStyle(Color.statusDestructive)
                            .multilineTextAlignment(.trailing)
                    }
                    retrySendButton(text: retryText)
                }
                .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
    }

    private func retrySendButton(text: String) -> some View {
        Button {
            onRetrySend(text)
        } label: {
            Label("Retry", systemImage: "arrow.clockwise")
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(Color.statusDestructive)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(Color.statusDestructive.opacity(0.1), in: Capsule())
                .overlay {
                    Capsule()
                        .stroke(Color.statusDestructive.opacity(0.2), lineWidth: 1)
                }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("knowledge.chat_retry_send")
    }
}

#if DEBUG
#Preview("Message Row") {
    MessageRow(
        item: ChatPreviewFixtures.timeline[0],
        retryingCouncilChildSessionId: nil,
        feedOptionActionModel: ChatPreviewActionModels.feedOptions(),
        onRetrySend: { _ in },
        onRetryCouncilCandidate: { _ in },
        onDigDeeper: { _ in },
        onShare: { _ in }
    )
    .padding()
    .background(Color.surfacePrimary)
}
#endif
