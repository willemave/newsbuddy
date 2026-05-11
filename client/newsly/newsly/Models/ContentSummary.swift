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
    let contentType: String
    let url: String
    let title: String?
    let source: String?
    let platform: String?
    let status: String
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
    let savedSource: String?
    private let cachedDisplayDate: Date?
    private let cachedProcessedDate: Date?
    private let cachedItemDate: Date?

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

    init(
        id: Int,
        contentType: String,
        url: String,
        title: String?,
        source: String?,
        platform: String?,
        status: String,
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
        self.savedSource = savedSource
        self.cachedDisplayDate = Self.parseDate(processedAt ?? createdAt)
        self.cachedProcessedDate = processedAt.flatMap(Self.parseDate)
        self.cachedItemDate = Self.parseDate(publicationDate ?? processedAt ?? createdAt)
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.init(
            id: try container.decode(Int.self, forKey: .id),
            contentType: try container.decode(String.self, forKey: .contentType),
            url: try container.decode(String.self, forKey: .url),
            title: try container.decodeIfPresent(String.self, forKey: .title),
            source: try container.decodeIfPresent(String.self, forKey: .source),
            platform: try container.decodeIfPresent(String.self, forKey: .platform),
            status: try container.decode(String.self, forKey: .status),
            shortSummary: try container.decodeIfPresent(String.self, forKey: .shortSummary),
            createdAt: try container.decode(String.self, forKey: .createdAt),
            processedAt: try container.decodeIfPresent(String.self, forKey: .processedAt),
            classification: try container.decodeIfPresent(String.self, forKey: .classification),
            publicationDate: try container.decodeIfPresent(String.self, forKey: .publicationDate),
            isRead: try container.decode(Bool.self, forKey: .isRead),
            isSavedToKnowledge: try container.decodeIfPresent(Bool.self, forKey: .isSavedToKnowledge) ?? false,
            imageUrl: try container.decodeIfPresent(String.self, forKey: .imageUrl),
            thumbnailUrl: try container.decodeIfPresent(String.self, forKey: .thumbnailUrl),
            primaryTopic: try container.decodeIfPresent(String.self, forKey: .primaryTopic),
            topComment: try container.decodeIfPresent(TopComment.self, forKey: .topComment),
            commentCount: try container.decodeIfPresent(Int.self, forKey: .commentCount),
            newsSummary: try container.decodeIfPresent(String.self, forKey: .newsSummary),
            newsKeyPoints: try container.decodeIfPresent([String].self, forKey: .newsKeyPoints),
            feedPreview: try container.decodeIfPresent(LongformFeedPreview.self, forKey: .feedPreview),
            artifactType: try container.decodeIfPresent(String.self, forKey: .artifactType),
            previewBullets: try container.decodeIfPresent([String].self, forKey: .previewBullets),
            reasonToRead: try container.decodeIfPresent(String.self, forKey: .reasonToRead),
            savedSource: try container.decodeIfPresent(String.self, forKey: .savedSource)
        )
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
        try container.encodeIfPresent(savedSource, forKey: .savedSource)
    }

    var contentTypeEnum: ContentType? {
        ContentType(rawValue: contentType)
    }

    var primaryTimestamp: String {
        publicationDate ?? processedAt ?? createdAt
    }

    var apiContentType: APIContentType? {
        APIContentType(rawValue: contentType)
    }

    var apiStatus: APIContentStatus? {
        APIContentStatus(rawValue: status)
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
        guard contentTypeEnum != .news else { return nil }
        return Self.normalizedText(shortSummary)
    }

    var secondaryLine: String? {
        summaryDisplayText
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
        ContentTimestampFormatter.compactRelativeText(from: primaryTimestamp)
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
            savedSource: savedSource
        )
    }

    /// The underlying Date parsed from the best available date field.
    var itemDate: Date? {
        cachedItemDate
    }

    /// Calendar day key for grouping (e.g. "2026-02-19").
    var calendarDayKey: String {
        guard let date = itemDate else { return "" }
        return Self.calendarDayFormatter.string(from: date)
    }
}
