//
//  ContentSummary.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation

struct ContentSummary: Codable, Identifiable, Equatable {
    struct TopComment: Codable, Equatable {
        let author: String
        let text: String
    }

    let id: Int
    let contentType: APIContentType
    let url: String
    let title: String?
    let source: String?
    let platform: String?
    let status: APIContentStatus
    let shortSummary: String?
    let createdAt: String
    let processedAt: String?
    let classification: String?
    let publicationDate: String?
    let isRead: Bool
    var isSavedToKnowledge: Bool
    let imageUrl: String?
    let thumbnailUrl: String?
    let primaryTopic: String?
    let topComment: TopComment?
    let commentCount: Int?
    let newsSummary: String?
    let newsKeyPoints: [String]?
    let feedPreview: LongformFeedPreview?
    let artifactType: String?
    let previewBullets: [String]?
    let reasonToRead: String?
    let keyTakeaway: String?
    let savedSource: String?
    private let cachedDisplayDate: Date?
    private let cachedProcessedDate: Date?
    private let cachedItemDate: Date?
    private let cachedCalendarDayKey: String

    enum CodingKeys: String, CodingKey {
        case id
        case contentType = "content_type"
        case url
        case title
        case source
        case platform
        case status
        case shortSummary = "short_summary"
        case createdAt = "created_at"
        case processedAt = "processed_at"
        case classification
        case publicationDate = "publication_date"
        case isRead = "is_read"
        case isSavedToKnowledge = "is_saved_to_knowledge"
        case imageUrl = "image_url"
        case thumbnailUrl = "thumbnail_url"
        case primaryTopic = "primary_topic"
        case topComment = "top_comment"
        case commentCount = "comment_count"
        case newsSummary = "news_summary"
        case newsKeyPoints = "news_key_points"
        case feedPreview = "feed_preview"
        case artifactType = "artifact_type"
        case previewBullets = "preview_bullets"
        case reasonToRead = "reason_to_read"
        case keyTakeaway = "key_takeaway"
        case savedSource = "saved_source"
    }

    private static let displayDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private static let processedDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd-yyyy"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private static let calendarDayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private static func parseDate(_ dateString: String) -> Date? {
        ContentTimestampFormatter.parse(dateString)
    }

    private static func decode<T: Decodable>(_ type: T.Type, from raw: [String: AnyCodable]?) -> T? {
        AnyCodableDecoding.decodeLenient(type, from: raw)
    }

    private static func topComment(from raw: [String: String]?) -> TopComment? {
        guard let raw, let text = raw["text"] else { return nil }
        return TopComment(author: raw["author"] ?? "", text: text)
    }

    init(
        id: Int,
        contentType: APIContentType,
        url: String,
        title: String?,
        source: String?,
        platform: String?,
        status: APIContentStatus,
        shortSummary: String?,
        createdAt: String,
        processedAt: String?,
        classification: String?,
        publicationDate: String?,
        isRead: Bool,
        isSavedToKnowledge: Bool,
        imageUrl: String? = nil,
        thumbnailUrl: String? = nil,
        primaryTopic: String? = nil,
        topComment: TopComment? = nil,
        commentCount: Int? = nil,
        newsSummary: String? = nil,
        newsKeyPoints: [String]? = nil,
        feedPreview: LongformFeedPreview? = nil,
        artifactType: String? = nil,
        previewBullets: [String]? = nil,
        reasonToRead: String? = nil,
        keyTakeaway: String? = nil,
        savedSource: String? = nil
    ) {
        self.id = id
        self.contentType = contentType
        self.url = url
        self.title = title
        self.source = source
        self.platform = platform
        self.status = status
        self.shortSummary = shortSummary
        self.createdAt = createdAt
        self.processedAt = processedAt
        self.classification = classification
        self.publicationDate = publicationDate
        self.isRead = isRead
        self.isSavedToKnowledge = isSavedToKnowledge
        self.imageUrl = imageUrl
        self.thumbnailUrl = thumbnailUrl
        self.primaryTopic = primaryTopic
        self.topComment = topComment
        self.commentCount = commentCount
        self.newsSummary = newsSummary
        self.newsKeyPoints = newsKeyPoints
        self.feedPreview = feedPreview
        self.artifactType = artifactType
        self.previewBullets = previewBullets
        self.reasonToRead = reasonToRead
        self.keyTakeaway = keyTakeaway
        self.savedSource = savedSource
        let displayDate = Self.parseDate(processedAt ?? createdAt)
        let processedDate = processedAt.flatMap(Self.parseDate)
        let itemDate = Self.parseDate(publicationDate ?? processedAt ?? createdAt)
        self.cachedDisplayDate = displayDate
        self.cachedProcessedDate = processedDate
        self.cachedItemDate = itemDate
        self.cachedCalendarDayKey = itemDate.map { Self.calendarDayFormatter.string(from: $0) } ?? ""
    }

    init(api response: APIContentSummaryResponse) {
        self.init(
            id: response.id,
            contentType: response.contentType,
            url: response.url,
            title: response.title,
            source: response.source,
            platform: response.platform,
            status: response.status,
            shortSummary: response.shortSummary,
            createdAt: response.createdAt,
            processedAt: response.processedAt,
            classification: response.classification?.rawValue,
            publicationDate: response.publicationDate,
            isRead: response.isRead,
            isSavedToKnowledge: response.isSavedToKnowledge,
            imageUrl: response.imageUrl,
            thumbnailUrl: response.thumbnailUrl,
            primaryTopic: response.primaryTopic,
            topComment: Self.topComment(from: response.topComment),
            commentCount: response.commentCount,
            newsSummary: response.newsSummary,
            newsKeyPoints: response.newsKeyPoints,
            feedPreview: Self.decode(LongformFeedPreview.self, from: response.feedPreview),
            artifactType: response.artifactType,
            previewBullets: response.previewBullets,
            reasonToRead: response.reasonToRead,
            keyTakeaway: response.keyTakeaway,
            savedSource: response.savedSource?.rawValue
        )
    }

    init(from decoder: Decoder) throws {
        self.init(api: try APIContentSummaryResponse(from: decoder))
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(contentType, forKey: .contentType)
        try container.encode(url, forKey: .url)
        try container.encodeIfPresent(title, forKey: .title)
        try container.encodeIfPresent(source, forKey: .source)
        try container.encodeIfPresent(platform, forKey: .platform)
        try container.encode(status, forKey: .status)
        try container.encodeIfPresent(shortSummary, forKey: .shortSummary)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encodeIfPresent(processedAt, forKey: .processedAt)
        try container.encodeIfPresent(classification, forKey: .classification)
        try container.encodeIfPresent(publicationDate, forKey: .publicationDate)
        try container.encode(isRead, forKey: .isRead)
        try container.encode(isSavedToKnowledge, forKey: .isSavedToKnowledge)
        try container.encodeIfPresent(imageUrl, forKey: .imageUrl)
        try container.encodeIfPresent(thumbnailUrl, forKey: .thumbnailUrl)
        try container.encodeIfPresent(primaryTopic, forKey: .primaryTopic)
        try container.encodeIfPresent(topComment, forKey: .topComment)
        try container.encodeIfPresent(commentCount, forKey: .commentCount)
        try container.encodeIfPresent(newsSummary, forKey: .newsSummary)
        try container.encodeIfPresent(newsKeyPoints, forKey: .newsKeyPoints)
        try container.encodeIfPresent(feedPreview, forKey: .feedPreview)
        try container.encodeIfPresent(artifactType, forKey: .artifactType)
        try container.encodeIfPresent(previewBullets, forKey: .previewBullets)
        try container.encodeIfPresent(reasonToRead, forKey: .reasonToRead)
        try container.encodeIfPresent(keyTakeaway, forKey: .keyTakeaway)
        try container.encodeIfPresent(savedSource, forKey: .savedSource)
    }

    var primaryTimestamp: String {
        publicationDate ?? processedAt ?? createdAt
    }

    var displayTitle: String {
        title ?? "Untitled"
    }

    private static func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    var summaryDisplayText: String? {
        guard contentType != .news else { return nil }
        return Self.normalizedText(shortSummary)
    }

    var secondaryLine: String? {
        summaryDisplayText
    }

    var keyTakeawayDisplayText: String? {
        guard contentType != .news else { return nil }
        return Self.normalizedText(keyTakeaway)
    }

    /// Discussion snippet for feed card preview
    var discussionSnippet: (author: String, text: String)? {
        if let comment = topComment {
            let author = comment.author.trimmingCharacters(in: .whitespacesAndNewlines)
            let text = comment.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return nil }
            return (author.isEmpty ? "unknown" : author, text)
        }
        return nil
    }

    /// Display string for comment count (e.g., "42")
    var commentCountDisplay: String? {
        guard let count = commentCount, count > 0 else { return nil }
        return "\(count)"
    }

    var formattedDate: String {
        guard let date = cachedDisplayDate else {
            return "Date unknown"
        }

        return Self.displayDateFormatter.string(from: date)
    }

    var processedDateDisplay: String? {
        guard let date = cachedProcessedDate else {
            return nil
        }

        return Self.processedDateFormatter.string(from: date)
    }

    /// Relative time display for news items (e.g., "2h ago", "3d ago")
    var relativeTimeDisplay: String? {
        guard let date = cachedItemDate else { return nil }
        return ContentTimestampFormatter.compactRelativeText(from: date)
    }

    func updating(
        isRead: Bool? = nil,
        isSavedToKnowledge: Bool? = nil
    ) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: url,
            title: title,
            source: source,
            platform: platform,
            status: status,
            shortSummary: shortSummary,
            createdAt: createdAt,
            processedAt: processedAt,
            classification: classification,
            publicationDate: publicationDate,
            isRead: isRead ?? self.isRead,
            isSavedToKnowledge: isSavedToKnowledge ?? self.isSavedToKnowledge,
            imageUrl: imageUrl,
            thumbnailUrl: thumbnailUrl,
            primaryTopic: primaryTopic,
            topComment: topComment,
            commentCount: commentCount,
            newsSummary: newsSummary,
            newsKeyPoints: newsKeyPoints,
            feedPreview: feedPreview,
            artifactType: artifactType,
            previewBullets: previewBullets,
            reasonToRead: reasonToRead,
            keyTakeaway: keyTakeaway,
            savedSource: savedSource
        )
    }

    /// The underlying Date parsed from the best available date field.
    var itemDate: Date? {
        cachedItemDate
    }

    /// Calendar day key for grouping (e.g. "2026-02-19").
    var calendarDayKey: String {
        cachedCalendarDayKey
    }
}
