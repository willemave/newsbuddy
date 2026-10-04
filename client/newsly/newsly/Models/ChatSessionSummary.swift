//
//  ChatSessionSummary.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

/// Summary of a chat session for list view
struct ChatSessionSummary: Identifiable, Hashable {
    static func == (lhs: ChatSessionSummary, rhs: ChatSessionSummary) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
    let id: Int
    let contentId: Int?
    let newsItemId: Int?
    let title: String?
    let sessionType: String?
    let topic: String?
    let llmProvider: String
    let llmModel: String
    let createdAt: Date
    let updatedAt: Date?
    let lastMessageAt: Date?
    let articleTitle: String?
    let articleUrl: String?
    let articleSummary: String?
    let articleSource: String?
    let articleImageUrl: String?
    let articleThumbnailUrl: String?
    let hasPendingMessage: Bool
    let isWaitingForContent: Bool
    let isSavedToKnowledge: Bool
    let hasMessages: Bool
    let lastMessagePreview: String?
    let lastMessageRole: String?
    let lastActivityDate: Date

    init(
        id: Int,
        contentId: Int?,
        newsItemId: Int? = nil,
        title: String?,
        sessionType: String?,
        topic: String?,
        llmProvider: String,
        llmModel: String,
        createdAt: Date,
        updatedAt: Date?,
        lastMessageAt: Date?,
        articleTitle: String?,
        articleUrl: String?,
        articleSummary: String?,
        articleSource: String?,
        articleImageUrl: String? = nil,
        articleThumbnailUrl: String? = nil,
        hasPendingMessage: Bool = false,
        isWaitingForContent: Bool = false,
        isSavedToKnowledge: Bool = false,
        hasMessages: Bool = true,
        lastMessagePreview: String?,
        lastMessageRole: String?
    ) {
        self.id = id
        self.contentId = contentId
        self.newsItemId = newsItemId
        self.title = title
        self.sessionType = sessionType
        self.topic = topic
        self.llmProvider = llmProvider
        self.llmModel = llmModel
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.lastMessageAt = lastMessageAt
        self.articleTitle = articleTitle
        self.articleUrl = articleUrl
        self.articleSummary = articleSummary
        self.articleSource = articleSource
        self.articleImageUrl = articleImageUrl
        self.articleThumbnailUrl = articleThumbnailUrl
        self.hasPendingMessage = hasPendingMessage
        self.isWaitingForContent = isWaitingForContent
        self.isSavedToKnowledge = isSavedToKnowledge
        self.hasMessages = hasMessages
        self.lastMessagePreview = lastMessagePreview
        self.lastMessageRole = lastMessageRole
        self.lastActivityDate = lastMessageAt ?? createdAt
    }

    init(api response: APIChatSessionSummary) {
        self.init(
            id: response.id,
            contentId: response.contentId,
            newsItemId: response.newsItemId,
            title: response.title,
            sessionType: response.sessionType,
            topic: response.topic,
            llmProvider: response.llmProvider,
            llmModel: response.llmModel,
            createdAt: response.createdAt,
            updatedAt: response.updatedAt,
            lastMessageAt: response.lastMessageAt,
            articleTitle: response.articleTitle,
            articleUrl: response.articleUrl,
            articleSummary: response.articleSummary,
            articleSource: response.articleSource,
            articleImageUrl: response.articleImageUrl,
            articleThumbnailUrl: response.articleThumbnailUrl,
            hasPendingMessage: response.hasPendingMessage,
            isWaitingForContent: response.isWaitingForContent,
            isSavedToKnowledge: response.isSavedToKnowledge,
            hasMessages: response.hasMessages,
            lastMessagePreview: response.lastMessagePreview,
            lastMessageRole: response.lastMessageRole
        )
    }


    /// True if the session has a message currently being processed
    var isProcessing: Bool {
        hasPendingMessage
    }

    /// True while a Share Sheet chat waits for its source and first turn.
    var isPreparingChat: Bool {
        isWaitingForContent
    }

    /// True if the session has any messages
    var hasAnyMessages: Bool {
        hasMessages
    }

    /// True if this is a saved article with no chat messages yet
    var isEmptyKnowledgeSave: Bool {
        isSavedToKnowledge && !hasAnyMessages
    }

    var isKnowledgeSession: Bool {
        switch sessionType {
        case "knowledge_chat", "assistant_quick", "article_brain", "topic":
            return true
        default:
            return false
        }
    }

    var displayTitle: String {
        title ?? articleTitle ?? "Chat"
    }

    var displaySubtitle: String? {
        if let topic = topic, !topic.isEmpty {
            return topic
        }
        // For empty knowledge saves, show the source
        if isEmptyKnowledgeSave, let source = articleSource {
            return source
        }
        if sessionType == "knowledge_chat", let articleTitle = articleTitle {
            return "About: \(articleTitle)"
        }
        if sessionType == "article_brain", let articleTitle = articleTitle {
            return "About: \(articleTitle)"
        }
        return nil
    }

    var providerDisplayName: String {
        switch llmProvider.lowercased() {
        case "openai":
            switch llmModel {
            case "openai:gpt-5.6-sol":
                return ChatModelProvider.openai.chatDisplayName
            case "openai:gpt-5.6-terra":
                return "GPT-5.6 Terra"
            case "openai:gpt-5.5":
                return "GPT-5.5"
            default:
                return "GPT"
            }
        case "anthropic":
            return "Claude"
        case "google":
            return "Gemini"
        case "deep_research":
            return "Deep Research"
        default:
            return llmProvider.capitalized
        }
    }

    /// Returns a fallback SF Symbol if custom icon is not available
    var providerIconFallback: String {
        switch llmProvider.lowercased() {
        case "openai":
            return "brain.head.profile"
        case "anthropic":
            return "sparkles"
        case "google":
            return "diamond"
        case "deep_research":
            return "magnifyingglass.circle.fill"
        default:
            return "cpu"
        }
    }

    /// Whether this is a deep research session
    var isDeepResearch: Bool {
        sessionType == "deep_research" || llmProvider.lowercased() == "deep_research"
    }

    var canStartDeepResearch: Bool {
        !isDeepResearch
    }

}

struct ChatSessionListResponse {
    let sessions: [ChatSessionSummary]
    let meta: PaginationMetadata

    init(sessions: [ChatSessionSummary], meta: PaginationMetadata) {
        self.sessions = sessions
        self.meta = meta
    }

    init(api response: APIChatSessionListResponse) {
        self.init(
            sessions: response.sessions.map(ChatSessionSummary.init(api:)),
            meta: response.meta
        )
    }
}
