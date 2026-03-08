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
    let isFavorite: Bool?
    let hasMessages: Bool?
    let lastMessagePreview: String?
    let lastMessageRole: String?

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
        case isFavorite = "is_favorite"
        case hasMessages = "has_messages"
        case lastMessagePreview = "last_message_preview"
        case lastMessageRole = "last_message_role"
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

    /// True if the linked content is favorited
    var isFavorited: Bool {
        isFavorite ?? false
    }

    /// True if the session has any messages
    var hasAnyMessages: Bool {
        hasMessages ?? true
    }

    /// True if this is a favorited article with no chat messages yet
    var isEmptyFavorite: Bool {
        isFavorited && !hasAnyMessages
    }

    var displayTitle: String {
        title ?? articleTitle ?? "Chat"
    }

    var displaySubtitle: String? {
        if let topic = topic, !topic.isEmpty {
            return topic
        }
        if sessionType == "daily_digest_brain" {
            return "About your daily digest"
        }
        // For empty favorites, show the source
        if isEmptyFavorite, let source = articleSource {
            return source
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

    /// Icon name for the session type (used in chat list)
    var sessionTypeIconName: String {
        switch sessionType {
        case "voice_live":
            return "waveform.and.mic"
        case "deep_research":
            return "magnifyingglass.circle.fill"
        case "topic":
            return "text.magnifyingglass"
        case "daily_digest_brain":
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
        case "voice_live":
            return "Live Voice"
        case "deep_research":
            return "Deep Research"
        case "topic":
            return "Search"
        case "daily_digest_brain":
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
