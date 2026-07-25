//
//  ChatSessionSummary.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation

/// Summary of a chat session for list view
struct ChatSessionSummary: Codable, Identifiable, Hashable {
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
    let hasPendingMessage: Bool?
    let isWaitingForContent: Bool?
    private let savedToKnowledgeValue: Bool?
    let hasMessages: Bool?
    let lastMessagePreview: String?
    let lastMessageRole: String?
    let councilMode: Bool?
    let activeChildSessionId: Int?
    private let cachedLastActivityDate: Date?

    enum CodingKeys: String, CodingKey {
        case id
        case contentId = "content_id"
        case newsItemId = "news_item_id"
        case title
        case sessionType = "session_type"
        case topic
        case llmProvider = "llm_provider"
        case llmModel = "llm_model"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case lastMessageAt = "last_message_at"
        case articleTitle = "article_title"
        case articleUrl = "article_url"
        case articleSummary = "article_summary"
        case articleSource = "article_source"
        case articleImageUrl = "article_image_url"
        case articleThumbnailUrl = "article_thumbnail_url"
        case hasPendingMessage = "has_pending_message"
        case isWaitingForContent = "is_waiting_for_content"
        case savedToKnowledgeValue = "is_saved_to_knowledge"
        case hasMessages = "has_messages"
        case lastMessagePreview = "last_message_preview"
        case lastMessageRole = "last_message_role"
        case councilMode = "council_mode"
        case activeChildSessionId = "active_child_session_id"
    }

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
        hasPendingMessage: Bool?,
        isWaitingForContent: Bool? = nil,
        isSavedToKnowledge: Bool?,
        hasMessages: Bool?,
        lastMessagePreview: String?,
        lastMessageRole: String?,
        councilMode: Bool? = nil,
        activeChildSessionId: Int? = nil
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
        self.savedToKnowledgeValue = isSavedToKnowledge
        self.hasMessages = hasMessages
        self.lastMessagePreview = lastMessagePreview
        self.lastMessageRole = lastMessageRole
        self.councilMode = councilMode
        self.activeChildSessionId = activeChildSessionId
        self.cachedLastActivityDate = lastMessageAt ?? createdAt
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
            lastMessageRole: response.lastMessageRole,
            councilMode: response.councilMode,
            activeChildSessionId: response.activeChildSessionId
        )
    }

    init(from decoder: Decoder) throws {
        self.init(api: try APIChatSessionSummary(from: decoder))
    }

    // Decoding goes through the generated APIChatSessionSummary, which parses wire
    // date strings into Date. Encode must stay symmetric (dates back to canonical
    // strings) — synthesized Encodable would emit numeric timestamps instead.
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encodeIfPresent(contentId, forKey: .contentId)
        try container.encodeIfPresent(newsItemId, forKey: .newsItemId)
        try container.encodeIfPresent(title, forKey: .title)
        try container.encodeIfPresent(sessionType, forKey: .sessionType)
        try container.encodeIfPresent(topic, forKey: .topic)
        try container.encode(llmProvider, forKey: .llmProvider)
        try container.encode(llmModel, forKey: .llmModel)
        try container.encode(ServerDate.format(createdAt), forKey: .createdAt)
        try container.encodeIfPresent(updatedAt.map(ServerDate.format), forKey: .updatedAt)
        try container.encodeIfPresent(lastMessageAt.map(ServerDate.format), forKey: .lastMessageAt)
        try container.encodeIfPresent(articleTitle, forKey: .articleTitle)
        try container.encodeIfPresent(articleUrl, forKey: .articleUrl)
        try container.encodeIfPresent(articleSummary, forKey: .articleSummary)
        try container.encodeIfPresent(articleSource, forKey: .articleSource)
        try container.encodeIfPresent(articleImageUrl, forKey: .articleImageUrl)
        try container.encodeIfPresent(articleThumbnailUrl, forKey: .articleThumbnailUrl)
        try container.encodeIfPresent(hasPendingMessage, forKey: .hasPendingMessage)
        try container.encodeIfPresent(isWaitingForContent, forKey: .isWaitingForContent)
        try container.encodeIfPresent(savedToKnowledgeValue, forKey: .savedToKnowledgeValue)
        try container.encodeIfPresent(hasMessages, forKey: .hasMessages)
        try container.encodeIfPresent(lastMessagePreview, forKey: .lastMessagePreview)
        try container.encodeIfPresent(lastMessageRole, forKey: .lastMessageRole)
        try container.encodeIfPresent(councilMode, forKey: .councilMode)
        try container.encodeIfPresent(activeChildSessionId, forKey: .activeChildSessionId)
    }

    private static let displayDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    /// True if the session has a message currently being processed
    var isProcessing: Bool {
        hasPendingMessage ?? false
    }

    /// True while a Share Sheet chat waits for its source and first turn.
    var isPreparingChat: Bool {
        isWaitingForContent ?? false
    }

    /// True if the linked content is saved to knowledge
    var isSavedToKnowledge: Bool {
        savedToKnowledgeValue ?? false
    }

    /// True if the session has any messages
    var hasAnyMessages: Bool {
        hasMessages ?? true
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

    var formattedDate: String {
        guard let date = cachedLastActivityDate else {
            return "Date unknown"
        }

        return Self.displayDateFormatter.string(from: date)
    }

    var lastActivityDate: Date? {
        cachedLastActivityDate
    }

    var providerDisplayName: String {
        switch llmProvider.lowercased() {
        case "openai":
            return llmModel == "openai:gpt-5.5" ? "GPT-5.5" : "GPT"
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

    /// Returns the custom asset icon name for the provider
    var providerIconAsset: String? {
        switch llmProvider.lowercased() {
        case "openai":
            return "openai-icon"
        case "anthropic":
            return "claude-icon"
        case "google":
            return "gemini-icon"
        case "deep_research":
            return "deep-research-icon"
        default:
            return nil
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

    var isCouncilMode: Bool {
        councilMode ?? false
    }

    var canStartCouncil: Bool {
        !isCouncilMode && sessionType != "deep_research"
    }

    var canStartDeepResearch: Bool {
        !isDeepResearch && !isCouncilMode
    }

    /// Icon name for the session type (used in chat list)
    var sessionTypeIconName: String {
        switch sessionType {
        case "knowledge_chat":
            return "bubble.left.and.bubble.right.fill"
        case "assistant_quick":
            return "sparkle.magnifyingglass"
        case "deep_research":
            return "magnifyingglass.circle.fill"
        case "weekly_discovery":
            return "calendar.badge.plus"
        case "topic":
            return "text.magnifyingglass"
        case "article_brain":
            return "doc.text.magnifyingglass"
        case "ad_hoc":
            return "bubble.left.and.bubble.right"
        default:
            return "bubble.left"
        }
    }

    /// Human-readable label for the session type
    var sessionTypeLabel: String {
        switch sessionType {
        case "knowledge_chat":
            return "Knowledge"
        case "assistant_quick":
            return "Assistant"
        case "deep_research":
            return "Deep Research"
        case "weekly_discovery":
            return "Weekly Discovery"
        case "topic":
            return "Search"
        case "article_brain":
            return "Dig Deeper"
        case "ad_hoc":
            return "Chat"
        default:
            return "Chat"
        }
    }

}

struct ChatSessionListResponse: Codable {
    let sessions: [ChatSessionSummary]
    let meta: PaginationMetadata

    init(sessions: [ChatSessionSummary], meta: PaginationMetadata) {
        self.sessions = sessions
        self.meta = meta
    }

    init(from decoder: Decoder) throws {
        let response = try APIChatSessionListResponse(from: decoder)
        self.init(
            sessions: response.sessions.map(ChatSessionSummary.init(api:)),
            meta: response.meta
        )
    }
}
