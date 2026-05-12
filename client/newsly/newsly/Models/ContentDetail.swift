//
//  ContentDetail.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation

private extension String {
    var nonEmptyTrimmed: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct RelevantLink: Identifiable, Hashable {
    let url: String
    let title: String?
    let reason: String
    let source: String?

    var id: String { url }

    init(url: String, title: String?, reason: String, source: String? = nil) {
        self.url = url
        self.title = title?.nonEmptyTrimmed
        self.reason = reason
        self.source = source?.nonEmptyTrimmed
    }

    init?(metadata: [String: Any]) {
        guard let url = metadata["url"] as? String,
              !url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }

        self.url = url
        self.title = (metadata["title"] as? String)?.nonEmptyTrimmed
        self.reason = (metadata["reason"] as? String)?.nonEmptyTrimmed
            ?? "Useful supporting context from the article."
        self.source = (metadata["source"] as? String)?.nonEmptyTrimmed
    }
}

typealias InterestingExternalLink = RelevantLink

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
    var isSavedToKnowledge: Bool
    let summary: String?
    let shortSummary: String?
    let summaryKind: String?
    let summaryVersion: Int?
    let structuredSummaryRaw: [String: AnyCodable]?
    let longformArtifactRaw: [String: AnyCodable]?
    let feedPreview: LongformFeedPreview?
    let artifactType: String?
    let previewBullets: [String]?
    let reasonToRead: String?
    let bulletPoints: [BulletPoint]
    let quotes: [Quote]
    let topics: [String]
    let fullMarkdown: String?
    let bodyAvailable: Bool
    let bodyKind: String?
    let bodyFormat: String?
    let imageUrl: String?
    let thumbnailUrl: String?
    let newsArticleURL: String?
    let newsDiscussionURL: String?
    let newsKeyPoints: [String]?
    let newsSummary: String?
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
        case isSavedToKnowledge = "is_saved_to_knowledge"
        case summary
        case shortSummary = "short_summary"
        case summaryKind = "summary_kind"
        case summaryVersion = "summary_version"
        case structuredSummaryRaw = "structured_summary"
        case longformArtifactRaw = "longform_artifact"
        case feedPreview = "feed_preview"
        case artifactType = "artifact_type"
        case previewBullets = "preview_bullets"
        case reasonToRead = "reason_to_read"
        case bulletPoints = "bullet_points"
        case quotes
        case topics
        case fullMarkdown = "full_markdown"
        case bodyAvailable = "body_available"
        case bodyKind = "body_kind"
        case bodyFormat = "body_format"
        case imageUrl = "image_url"
        case thumbnailUrl = "thumbnail_url"
        case newsArticleURL = "news_article_url"
        case newsDiscussionURL = "news_discussion_url"
        case newsKeyPoints = "news_key_points"
        case newsSummary = "news_summary"
        case detectedFeed = "detected_feed"
        case canSubscribe = "can_subscribe"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)

        id = try container.decode(Int.self, forKey: .id)
        contentType = try container.decode(String.self, forKey: .contentType)
        url = try container.decode(String.self, forKey: .url)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        status = try container.decode(String.self, forKey: .status)
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        retryCount = try container.decodeIfPresent(Int.self, forKey: .retryCount) ?? 0
        metadata = try container.decodeIfPresent([String: AnyCodable].self, forKey: .metadata) ?? [:]
        createdAt = try container.decode(String.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        processedAt = try container.decodeIfPresent(String.self, forKey: .processedAt)
        checkedOutBy = try container.decodeIfPresent(String.self, forKey: .checkedOutBy)
        checkedOutAt = try container.decodeIfPresent(String.self, forKey: .checkedOutAt)
        publicationDate = try container.decodeIfPresent(String.self, forKey: .publicationDate)
        isRead = try container.decodeIfPresent(Bool.self, forKey: .isRead) ?? false
        isSavedToKnowledge = try container.decodeIfPresent(Bool.self, forKey: .isSavedToKnowledge) ?? false
        summary = try container.decodeIfPresent(String.self, forKey: .summary)
        shortSummary = try container.decodeIfPresent(String.self, forKey: .shortSummary)
        summaryKind = try container.decodeIfPresent(String.self, forKey: .summaryKind)
        summaryVersion = try container.decodeIfPresent(Int.self, forKey: .summaryVersion)
        structuredSummaryRaw = try container.decodeIfPresent([String: AnyCodable].self, forKey: .structuredSummaryRaw)
        longformArtifactRaw = try container.decodeIfPresent([String: AnyCodable].self, forKey: .longformArtifactRaw)
        feedPreview = try container.decodeIfPresent(LongformFeedPreview.self, forKey: .feedPreview)
        artifactType = try container.decodeIfPresent(String.self, forKey: .artifactType)
        previewBullets = try container.decodeIfPresent([String].self, forKey: .previewBullets)
        reasonToRead = try container.decodeIfPresent(String.self, forKey: .reasonToRead)
        bulletPoints = try container.decodeIfPresent([BulletPoint].self, forKey: .bulletPoints) ?? []
        quotes = try container.decodeIfPresent([Quote].self, forKey: .quotes) ?? []
        topics = try container.decodeIfPresent([String].self, forKey: .topics) ?? []
        fullMarkdown = try container.decodeIfPresent(String.self, forKey: .fullMarkdown)
        bodyAvailable = try container.decodeIfPresent(Bool.self, forKey: .bodyAvailable) ?? false
        bodyKind = try container.decodeIfPresent(String.self, forKey: .bodyKind)
        bodyFormat = try container.decodeIfPresent(String.self, forKey: .bodyFormat)
        imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        thumbnailUrl = try container.decodeIfPresent(String.self, forKey: .thumbnailUrl)
        newsArticleURL = try container.decodeIfPresent(String.self, forKey: .newsArticleURL)
        newsDiscussionURL = try container.decodeIfPresent(String.self, forKey: .newsDiscussionURL)
        newsKeyPoints = try container.decodeIfPresent([String].self, forKey: .newsKeyPoints)
        newsSummary = try container.decodeIfPresent(String.self, forKey: .newsSummary)
        detectedFeed = try container.decodeIfPresent(DetectedFeed.self, forKey: .detectedFeed)
        canSubscribe = try container.decodeIfPresent(Bool.self, forKey: .canSubscribe)

        if let displayTitle = try container.decodeIfPresent(String.self, forKey: .displayTitle),
           !displayTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.displayTitle = displayTitle
        } else if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.displayTitle = title
        } else {
            self.displayTitle = url
        }
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

    var primaryTimestamp: String {
        publicationDate ?? processedAt ?? createdAt
    }

    var detailTypeLabel: String {
        if contentTypeEnum == .news,
           let name = newsMetadata?.aggregator?.name,
           !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return name
        }
        return contentTypeEnum?.rawValue.capitalized ?? "Article"
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

    private func normalizedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func normalizedURLKey(_ value: String?) -> String? {
        normalizedText(value)?.lowercased()
    }

    var resolvedNewsSummaryText: String? {
        normalizedText(newsMetadata?.summary?.summary)
            ?? normalizedText(newsSummary)
            ?? normalizedText(summary)
            ?? normalizedText(shortSummary)
    }

    var resolvedNewsArticleURL: String? {
        normalizedText(newsMetadata?.summary?.articleURL)
            ?? normalizedText(newsArticleURL)
    }

    var resolvedNewsKeyPoints: [String] {
        let metadataKeyPoints = newsMetadata?.summary?.keyPoints ?? []
        let values = metadataKeyPoints.isEmpty ? (newsKeyPoints ?? []) : metadataKeyPoints

        var seen: Set<String> = []
        var result: [String] = []

        for value in values {
            guard let normalized = normalizedText(value) else { continue }
            let key = normalized.lowercased()
            if seen.insert(key).inserted {
                result.append(normalized)
            }
        }

        return result
    }

    var interestingExternalLinks: [RelevantLink] {
        guard let rawLinks = metadata["interesting_external_links"]?.value as? [[String: Any]] else {
            return []
        }

        var seen: Set<String> = []
        return rawLinks.compactMap { rawLink in
            guard let link = InterestingExternalLink(metadata: rawLink),
                  seen.insert(link.url).inserted else {
                return nil
            }
            return link
        }
    }

    var newsRelevantLinks: [RelevantLink] {
        guard contentTypeEnum == .news,
              let rawLinks = metadata["relevant_links"]?.value as? [[String: Any]] else {
            return []
        }

        var excluded = Set([
            normalizedURLKey(url),
            normalizedURLKey(newsArticleURL),
            normalizedURLKey(newsDiscussionURL),
            normalizedURLKey(newsMetadata?.discussionURL),
            normalizedURLKey(newsMetadata?.article?.url)
        ].compactMap { $0 })
        var result: [RelevantLink] = []

        for rawLink in rawLinks {
            guard let link = RelevantLink(metadata: rawLink),
                  let key = normalizedURLKey(link.url),
                  !excluded.contains(key) else {
                continue
            }
            excluded.insert(key)
            result.append(link)
        }
        return result
    }

    var relevantLinks: [RelevantLink] {
        if contentTypeEnum == .news {
            return newsRelevantLinks
        }
        return interestingExternalLinks
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

    var longformArtifact: LongformArtifactEnvelope? {
        let raw = longformArtifactRaw ?? structuredSummaryRaw
        guard resolvedSummaryKind == "longform_artifact",
              let raw else {
            return nil
        }

        let decoder = JSONDecoder()
        if let jsonData = try? JSONSerialization.data(withJSONObject: raw.mapValues { $0.value }) {
            return try? decoder.decode(LongformArtifactEnvelope.self, from: jsonData)
        }
        return nil
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
              (resolvedSummaryVersion == 1 || resolvedSummaryVersion == 2),
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
