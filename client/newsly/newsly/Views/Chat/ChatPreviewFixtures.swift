//
//  ChatPreviewFixtures.swift
//  newsly
//

import Foundation

#if DEBUG
enum ChatPreviewFixtures {
    static let timestamp = ServerDate.parse("2026-04-16T18:30:00Z")!

    static let userMessage = ChatMessage(
        id: 1,
        role: .user,
        timestamp: timestamp,
        content: "What are the strongest objections to the author's argument?",
        status: .completed
    )

    static let assistantMessage = ChatMessage(
        id: 2,
        role: .assistant,
        timestamp: timestamp,
        content: "The strongest objection is that the article treats a short-term signal as if it were a durable trend. A better test would compare several market cycles and separate adoption from novelty.",
        status: .completed
    )

    static let processSummaryMessage = ChatMessage(
        id: 3,
        role: .assistant,
        timestamp: timestamp,
        content: "Searched for counterarguments and compared them with the article's main claim.",
        displayType: .process_summary,
        processLabel: "Checked opposing evidence across three sources",
        status: .completed
    )

    static let failedUserItem = ChatTimelineItem(
        id: .local(UUID(uuidString: "11111111-1111-1111-1111-111111111111")!),
        message: ChatMessage(
            id: -1,
            role: .user,
            timestamp: timestamp,
            content: "Retry this question after the network returns.",
            status: .failed,
            error: "The network connection was lost."
        ),
        pendingMessageId: nil,
        retryText: "Retry this question after the network returns."
    )

    static let timeline: [ChatTimelineItem] = [
        ChatTimelineItem(
            id: ChatTimelineID.server(for: userMessage),
            message: userMessage,
            pendingMessageId: nil,
            retryText: nil
        ),
        ChatTimelineItem(
            id: ChatTimelineID.server(for: assistantMessage),
            message: assistantMessage,
            pendingMessageId: nil,
            retryText: nil
        ),
        ChatTimelineItem(
            id: ChatTimelineID.server(for: processSummaryMessage),
            message: processSummaryMessage,
            pendingMessageId: nil,
            retryText: nil
        ),
        failedUserItem
    ]

    static let session = ChatSessionSummary(
        id: 42,
        contentId: 7,
        title: "Preview Chat",
        sessionType: "knowledge_chat",
        topic: "Market structure",
        llmProvider: "openai",
        llmModel: "openai:gpt-5.6-terra",
        createdAt: timestamp,
        updatedAt: timestamp,
        lastMessageAt: timestamp,
        articleTitle: "A Careful Reading of Market Structure",
        articleUrl: "https://example.com/article",
        articleSummary: "A concise article arguing that a structural shift is visible in early indicators.",
        articleSource: "Example Journal",
        hasPendingMessage: false,
        isSavedToKnowledge: true,
        hasMessages: true,
        lastMessagePreview: "The strongest objection is...",
        lastMessageRole: "assistant"
    )
}

@MainActor
private final class PreviewAssistantFeedSubscribing: AssistantFeedSubscribing {
    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig {
        ScraperConfig(
            id: 1,
            scraperType: feedType,
            displayName: displayName ?? "Preview Feed",
            config: ["feed_url": AnyCodable(feedURL)],
            feedUrl: nil,
            limit: nil,
            isActive: true,
            createdAt: ChatPreviewFixtures.timestamp,
            stats: nil,
            subscriptionOutcome: nil,
            backfillTaskId: nil
        )
    }
}

@MainActor
private final class PreviewToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}

@MainActor
enum ChatPreviewActionModels {
    static func feedOptions() -> AssistantFeedOptionActionModel {
        AssistantFeedOptionActionModel(
            service: PreviewAssistantFeedSubscribing(),
            toastPresenter: PreviewToastPresenter()
        )
    }
}
#endif
