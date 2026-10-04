//
//  ChatSessionViewPreviews.swift
//  newsly
//

import SwiftUI

#if DEBUG
@MainActor
private func makeChatPreviewLifecycle() -> AppLifecycle {
    let lifecycle = AppLifecycle()
    lifecycle.record(.active)
    return lifecycle
}

@MainActor
private func makeChatPreviewSessionManager() -> ActiveChatSessionManager {
    ActiveChatSessionManager(
        messageCompletionRegistry: ChatMessageCompletionRegistry(
            statusService: PreviewUnavailableMessageStatusService()
        ),
        notificationService: PreviewNoopChatCompletionNotifier(),
        startsPolling: false
    )
}

private final class PreviewUnavailableMessageStatusService: MessageStatusFetching {
    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        throw CancellationError()
    }
}

@MainActor
private final class PreviewNoopChatCompletionNotifier: ChatCompletionNotifying {
    func showChatCompletedNotification(sessionId: Int, title: String, message: String) {}
}

#Preview {
    NavigationStack {
        ChatSessionView(route: ChatSessionRoute(session: ChatSessionSummary(
            id: 1,
            contentId: nil,
            title: "Test Chat",
            sessionType: "ad_hoc",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.6-terra",
            createdAt: ServerDate.parse("2025-11-28T12:00:00Z")!,
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )))
    }
    .environment(makeChatPreviewLifecycle())
    .environment(makeChatPreviewSessionManager())
}
#endif
