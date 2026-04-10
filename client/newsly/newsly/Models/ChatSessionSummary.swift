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
    let title: String?
    let sessionType: String?
    let topic: String?
    let llmProvider: String
    let llmModel: String
    let createdAt: String
    let updatedAt: String?
    let lastMessageAt: String?
    let articleTitle: String?
    let articleUrl: String?
    let articleSummary: String?
    let articleSource: String?
    let hasPendingMessage: Bool?
    private let savedToKnowledgeValue: Bool?
    let hasMessages: Bool?
    let lastMessagePreview: String?
    let lastMessageRole: String?
    let councilMode: Bool?
    let activeChildSessionId: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case contentId = "content_id"
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
        case hasPendingMessage = "has_pending_message"
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
        title: String?,
        sessionType: String?,
        topic: String?,
        llmProvider: String,
        llmModel: String,
        createdAt: String,
        updatedAt: String?,
        lastMessageAt: String?,
        articleTitle: String?,
        articleUrl: String?,
        articleSummary: String?,
        articleSource: String?,
        hasPendingMessage: Bool?,
        isSavedToKnowledge: Bool?,
        hasMessages: Bool?,
        lastMessagePreview: String?,
        lastMessageRole: String?,
        councilMode: Bool? = nil,
        activeChildSessionId: Int? = nil
    ) {
        self.id = id
        self.contentId = contentId
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
        self.hasPendingMessage = hasPendingMessage
        self.savedToKnowledgeValue = isSavedToKnowledge
        self.hasMessages = hasMessages
        self.lastMessagePreview = lastMessagePreview
        self.lastMessageRole = lastMessageRole
        self.councilMode = councilMode
        self.activeChildSessionId = activeChildSessionId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        contentId = try container.decodeIfPresent(Int.self, forKey: .contentId)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        sessionType = try container.decodeIfPresent(String.self, forKey: .sessionType)
        topic = try container.decodeIfPresent(String.self, forKey: .topic)
        llmProvider = try container.decode(String.self, forKey: .llmProvider)
        llmModel = try container.decode(String.self, forKey: .llmModel)
        createdAt = try container.decode(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        lastMessageAt = try container.decodeIfPresent(String.self, forKey: .lastMessageAt)
        articleTitle = try container.decodeIfPresent(String.self, forKey: .articleTitle)
        articleUrl = try container.decodeIfPresent(String.self, forKey: .articleUrl)
        articleSummary = try container.decodeIfPresent(String.self, forKey: .articleSummary)
        articleSource = try container.decodeIfPresent(String.self, forKey: .articleSource)
        hasPendingMessage = try container.decodeIfPresent(Bool.self, forKey: .hasPendingMessage)
        savedToKnowledgeValue = try container.decodeIfPresent(Bool.self, forKey: .savedToKnowledgeValue)
        hasMessages = try container.decodeIfPresent(Bool.self, forKey: .hasMessages)
        lastMessagePreview = try container.decodeIfPresent(String.self, forKey: .lastMessagePreview)
        lastMessageRole = try container.decodeIfPresent(String.self, forKey: .lastMessageRole)
        councilMode = try container.decodeIfPresent(Bool.self, forKey: .councilMode)
        activeChildSessionId = try container.decodeIfPresent(Int.self, forKey: .activeChildSessionId)
    }

    private static let iso8601WithFractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let iso8601Formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let utcMicrosecondsFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        formatter.timeZone = TimeZone(abbreviation: "UTC")
        return formatter
    }()

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
        if sessionType == "daily_digest_brain" || sessionType == "news_digest_brain" {
            return "About your news digest"
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
        let dateString = lastMessageAt ?? createdAt
        guard let date = Self.parseDate(dateString) else {
            return "Date unknown"
        }

        return Self.displayDateFormatter.string(from: date)
    }

    var providerDisplayName: String {
        switch llmProvider.lowercased() {
        case "openai":
            return llmModel == "openai:gpt-5.4" ? "GPT-5.4" : "GPT"
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
        case "daily_digest_brain", "news_digest_brain":
            return "calendar.badge.clock"
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
        case "daily_digest_brain", "news_digest_brain":
            return "Daily Digest"
        case "article_brain":
            return "Dig Deeper"
        case "ad_hoc":
            return "Chat"
        default:
            return "Chat"
        }
    }

    private static func parseDate(_ dateString: String) -> Date? {
        if let date = Self.iso8601WithFractionalFormatter.date(from: dateString) {
            return date
        }
        if let date = Self.iso8601Formatter.date(from: dateString) {
            return date
        }
        return Self.utcMicrosecondsFormatter.date(from: dateString)
    }
}
