//
//  ContentDetail.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation

struct ContentDetail: Codable, Identifiable {
    let id: Int
    let contentType: String
    let url: String
    let title: String?
    let displayTitle: String
    let source: String?
    let status: String
    let errorMessage: String?
    let retryCount: Int
    let metadata: [String: AnyCodable]
    let createdAt: String
    let updatedAt: String?
    let processedAt: String?
    let checkedOutBy: String?
    let checkedOutAt: String?
    let publicationDate: String?
    var isRead: Bool
    var isFavorited: Bool
    let summary: String?
    let shortSummary: String?
    let summaryKind: String?
    let summaryVersion: Int?
    let structuredSummaryRaw: [String: AnyCodable]?
    let bulletPoints: [BulletPoint]
    let quotes: [Quote]
    let topics: [String]
    let fullMarkdown: String?
    let bodyAvailable: Bool
    let bodyKind: String?
    let bodyFormat: String?
    let imageUrl: String?
    let thumbnailUrl: String?
    let detectedFeed: DetectedFeed?
    let canSubscribe: Bool?

    enum CodingKeys: String, CodingKey {
        case id
        case contentType = "content_type"
        case url
        case title
        case displayTitle = "display_title"
        case source
        case status
        case errorMessage = "error_message"
        case retryCount = "retry_count"
        case metadata
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case processedAt = "processed_at"
        case checkedOutBy = "checked_out_by"
        case checkedOutAt = "checked_out_at"
        case publicationDate = "publication_date"
        case isRead = "is_read"
        case isFavorited = "is_favorited"
        case summary
        case shortSummary = "short_summary"
        case summaryKind = "summary_kind"
        case summaryVersion = "summary_version"
        case structuredSummaryRaw = "structured_summary"
        case bulletPoints = "bullet_points"
        case quotes
        case topics
        case fullMarkdown = "full_markdown"
        case bodyAvailable = "body_available"
        case bodyKind = "body_kind"
        case bodyFormat = "body_format"
        case imageUrl = "image_url"
        case thumbnailUrl = "thumbnail_url"
        case detectedFeed = "detected_feed"
        case canSubscribe = "can_subscribe"
    }
    
    var contentTypeEnum: ContentType? {
        ContentType(rawValue: contentType)
    }

    var apiContentType: APIContentType? {
        APIContentType(rawValue: contentType)
    }

    var apiStatus: APIContentStatus? {
        APIContentStatus(rawValue: status)
    }

    var apiSummaryKind: APISummaryKind? {
        APISummaryKind(rawValue: resolvedSummaryKind ?? "")
    }

    var apiSummaryVersion: APISummaryVersion? {
        guard let resolvedSummaryVersion else { return nil }
        return APISummaryVersion(rawValue: resolvedSummaryVersion)
    }
    
    var articleMetadata: ArticleMetadata? {
        guard apiContentType == .article else { return nil }
        
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        
        if let jsonData = try? JSONSerialization.data(withJSONObject: metadata.mapValues { $0.value }) {
            return try? decoder.decode(ArticleMetadata.self, from: jsonData)
        }
        return nil
    }
    
    var podcastMetadata: PodcastMetadata? {
        guard apiContentType == .podcast else { return nil }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        if let jsonData = try? JSONSerialization.data(withJSONObject: metadata.mapValues { $0.value }) {
            return try? decoder.decode(PodcastMetadata.self, from: jsonData)
        }
        return nil
    }

    var newsMetadata: NewsMetadata? {
        guard apiContentType == .news else { return nil }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        if let jsonData = try? JSONSerialization.data(withJSONObject: metadata.mapValues { $0.value }) {
            return try? decoder.decode(NewsMetadata.self, from: jsonData)
        }
        return nil
    }

    // MARK: - Summary Type Detection

    private var resolvedSummaryKind: String? {
        if let summaryKind { return summaryKind }
        return metadata["summary_kind"]?.value as? String
    }

    private var resolvedSummaryVersion: Int? {
        if let summaryVersion { return summaryVersion }
        if let version = metadata["summary_version"]?.value as? Int {
            return version
        }
        if let version = metadata["summary_version"]?.value as? Double {
            return Int(version)
        }
        return nil
    }

    /// Check if this content has an interleaved summary format
    var hasInterleavedSummary: Bool {
        resolvedSummaryKind == "long_interleaved"
    }

    /// Parse the raw summary as InterleavedSummary (returns nil if not interleaved format)
    var interleavedSummary: InterleavedSummary? {
        guard hasInterleavedSummary,
              resolvedSummaryVersion == 1,
              let raw = structuredSummaryRaw else {
            return nil
        }

        let decoder = JSONDecoder()
        if let jsonData = try? JSONSerialization.data(withJSONObject: raw.mapValues { $0.value }) {
            return try? decoder.decode(InterleavedSummary.self, from: jsonData)
        }
        return nil
    }

    /// Parse the raw summary as InterleavedSummaryV2 (returns nil if not v2 format)
    var interleavedSummaryV2: InterleavedSummaryV2? {
        guard hasInterleavedSummary,
              resolvedSummaryVersion == 2,
              let raw = structuredSummaryRaw else {
            return nil
        }

        let decoder = JSONDecoder()
        if let jsonData = try? JSONSerialization.data(withJSONObject: raw.mapValues { $0.value }) {
            return try? decoder.decode(InterleavedSummaryV2.self, from: jsonData)
        }
        return nil
    }

    /// Parse the raw summary as BulletedSummary (returns nil if not bulleted format)
    var bulletedSummary: BulletedSummary? {
        guard resolvedSummaryKind == "long_bullets",
              resolvedSummaryVersion == 1,
              let raw = structuredSummaryRaw else {
            return nil
        }

        let decoder = JSONDecoder()
        if let jsonData = try? JSONSerialization.data(withJSONObject: raw.mapValues { $0.value }) {
            return try? decoder.decode(BulletedSummary.self, from: jsonData)
        }
        return nil
    }

    /// Parse the raw summary as EditorialNarrativeSummary (returns nil if not editorial format)
    var editorialSummary: EditorialNarrativeSummary? {
        guard resolvedSummaryKind == "long_editorial_narrative",
              resolvedSummaryVersion == 1,
              let raw = structuredSummaryRaw else {
            return nil
        }

        let decoder = JSONDecoder()
        if let jsonData = try? JSONSerialization.data(withJSONObject: raw.mapValues { $0.value }) {
            return try? decoder.decode(EditorialNarrativeSummary.self, from: jsonData)
        }
        return nil
    }

    /// Parse the raw summary as StructuredSummary (returns nil if interleaved format)
    var structuredSummary: StructuredSummary? {
        guard resolvedSummaryKind == "long_structured",
              let raw = structuredSummaryRaw else {
            return nil
        }

        let decoder = JSONDecoder()
        if let jsonData = try? JSONSerialization.data(withJSONObject: raw.mapValues { $0.value }) {
            return try? decoder.decode(StructuredSummary.self, from: jsonData)
        }
        return nil
    }
}
