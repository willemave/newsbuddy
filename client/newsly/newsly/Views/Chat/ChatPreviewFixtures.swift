//
//  ChatPreviewFixtures.swift
//  newsly
//

import Foundation

#if DEBUG
enum ChatPreviewFixtures {
    static let timestamp = "2026-04-16T18:30:00Z"

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
        displayType: .processSummary,
        processLabel: "Checked opposing evidence across three sources",
        status: .completed
    )

    static let feedOption = AssistantFeedOption(
        id: "example-feed",
        title: "Policy Signals Weekly",
        siteURL: "https://example.com",
        feedURL: "https://example.com/feed.xml",
        feedType: "atom",
        feedFormat: "atom",
        description: "A concise weekly feed tracking policy and market signals.",
        rationale: "Useful because it covers the same topic with a slower publishing cadence.",
        evidenceURL: "https://example.com/policy-signals"
    )

    static let assistantWithFeedOptions = ChatMessage(
        id: 4,
        role: .assistant,
        timestamp: timestamp,
        content: "I found a feed that is likely worth tracking alongside this topic.",
        status: .completed,
        feedOptions: [feedOption]
    )

    static let councilCandidates: [CouncilCandidate] = [
        CouncilCandidate(
            personaId: "optimist",
            personaName: "Optimist",
            childSessionId: 101,
            content: "The upside case is that the author identified an early shift before it appears in lagging indicators.",
            status: "completed",
            order: 0
        ),
        CouncilCandidate(
            personaId: "skeptic",
            personaName: "Skeptic",
            childSessionId: 102,
            content: "The weak point is the lack of a base rate. Similar signals have faded quickly in prior cycles.",
            status: "completed",
            order: 1
        ),
        CouncilCandidate(
            personaId: "operator",
            personaName: "Operator",
            childSessionId: 103,
            content: "",
            status: "processing",
            order: 2
        )
    ]

    static let councilMessage = ChatMessage(
        id: 5,
        role: .assistant,
        timestamp: timestamp,
        content: "",
        status: .completed,
        councilCandidates: councilCandidates,
        activeCouncilChildSessionId: 101
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
        llmModel: "openai:gpt-5.4",
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
        lastMessageRole: "assistant",
        councilMode: true,
        activeChildSessionId: 101
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
            limit: nil,
            isActive: true,
            createdAt: ChatPreviewFixtures.timestamp,
            stats: nil
        )
    }
}

@MainActor
enum ChatPreviewActionModels {
    static func feedOptions() -> AssistantFeedOptionActionModel {
        AssistantFeedOptionActionModel(service: PreviewAssistantFeedSubscribing())
    }
}
#endif
