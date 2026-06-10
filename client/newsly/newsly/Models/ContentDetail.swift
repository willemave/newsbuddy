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

/// Decoded API payloads derived from metadata once, when `ContentDetail` is
/// decoded. Keeping this immutable preserves value-model semantics while avoiding
/// repeated JSON re-serialization from SwiftUI view bodies.
private struct ContentDetailDecodedPayloads {
    let articleMetadata: ArticleMetadata?
    let podcastMetadata: PodcastMetadata?
    let newsMetadata: NewsMetadata?
    let longformArtifact: LongformArtifactEnvelope?
    let interleavedSummary: InterleavedSummary?
    let interleavedSummaryV2: InterleavedSummaryV2?
    let bulletedSummary: BulletedSummary?
    let editorialSummary: EditorialNarrativeSummary?
    let structuredSummary: StructuredSummary?

    init(
        contentType: String,
        metadata: [String: AnyCodable],
        summaryKind: String?,
        summaryVersion: Int?,
        structuredSummaryRaw: [String: AnyCodable]?,
        longformArtifactRaw: [String: AnyCodable]?
    ) {
        let apiContentType = APIContentType(rawValue: contentType)
        articleMetadata = apiContentType == .article
            ? Self.decode(ArticleMetadata.self, from: metadata)
            : nil
        podcastMetadata = apiContentType == .podcast
            ? Self.decode(PodcastMetadata.self, from: metadata)
            : nil
        newsMetadata = apiContentType == .news
            ? Self.decode(NewsMetadata.self, from: metadata)
            : nil

        let resolvedSummaryKind = summaryKind ?? metadata["summary_kind"]?.value as? String
        let resolvedSummaryVersion = summaryVersion
            ?? (metadata["summary_version"]?.value as? Int)
            ?? (metadata["summary_version"]?.value as? Double).map(Int.init)
        let rawSummary = structuredSummaryRaw
        let rawArtifact = longformArtifactRaw ?? structuredSummaryRaw

        longformArtifact = resolvedSummaryKind == "longform_artifact"
            ? Self.decode(LongformArtifactEnvelope.self, from: rawArtifact)
            : nil
        interleavedSummary = resolvedSummaryKind == "long_interleaved" && resolvedSummaryVersion == 1
            ? Self.decode(InterleavedSummary.self, from: rawSummary)
            : nil
        interleavedSummaryV2 = resolvedSummaryKind == "long_interleaved" && resolvedSummaryVersion == 2
            ? Self.decode(InterleavedSummaryV2.self, from: rawSummary)
            : nil
        bulletedSummary = resolvedSummaryKind == "long_bullets" && resolvedSummaryVersion == 1
            ? Self.decode(BulletedSummary.self, from: rawSummary)
            : nil
        editorialSummary = resolvedSummaryKind == "long_editorial_narrative"
            && (resolvedSummaryVersion == 1 || resolvedSummaryVersion == 2)
            ? Self.decode(EditorialNarrativeSummary.self, from: rawSummary)
            : nil
        structuredSummary = resolvedSummaryKind == "long_structured"
            ? Self.decode(StructuredSummary.self, from: rawSummary)
            : nil
    }

    private static func decode<T: Decodable>(_ type: T.Type, from raw: [String: AnyCodable]?) -> T? {
        guard let raw else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let data = try? JSONSerialization.data(withJSONObject: raw.mapValues(\.value)) else {
            return nil
        }
        return try? decoder.decode(type, from: data)
    }
}

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

    private let decodedPayloads: ContentDetailDecodedPayloads

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
        let decodedContentType = try container.decode(String.self, forKey: .contentType)
        contentType = decodedContentType
        let decodedURL = try container.decode(String.self, forKey: .url)
        url = decodedURL
        let decodedTitle = try container.decodeIfPresent(String.self, forKey: .title)
        title = decodedTitle
        source = try container.decodeIfPresent(String.self, forKey: .source)
        status = try container.decode(String.self, forKey: .status)
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        retryCount = try container.decodeIfPresent(Int.self, forKey: .retryCount) ?? 0
        let decodedMetadata = try container.decodeIfPresent([String: AnyCodable].self, forKey: .metadata) ?? [:]
        metadata = decodedMetadata
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
        let decodedSummaryKind = try container.decodeIfPresent(String.self, forKey: .summaryKind)
        summaryKind = decodedSummaryKind
        let decodedSummaryVersion = try container.decodeIfPresent(Int.self, forKey: .summaryVersion)
        summaryVersion = decodedSummaryVersion
        let decodedStructuredSummaryRaw = try container.decodeIfPresent(
            [String: AnyCodable].self,
            forKey: .structuredSummaryRaw
        )
        structuredSummaryRaw = decodedStructuredSummaryRaw
        let decodedLongformArtifactRaw = try container.decodeIfPresent(
            [String: AnyCodable].self,
            forKey: .longformArtifactRaw
        )
        longformArtifactRaw = decodedLongformArtifactRaw
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

        if let decodedDisplayTitle = try container.decodeIfPresent(String.self, forKey: .displayTitle),
           !decodedDisplayTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.displayTitle = decodedDisplayTitle
        } else if let decodedTitle, !decodedTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.displayTitle = decodedTitle
        } else {
            self.displayTitle = decodedURL
        }

        decodedPayloads = ContentDetailDecodedPayloads(
            contentType: decodedContentType,
            metadata: decodedMetadata,
            summaryKind: decodedSummaryKind,
            summaryVersion: decodedSummaryVersion,
            structuredSummaryRaw: decodedStructuredSummaryRaw,
            longformArtifactRaw: decodedLongformArtifactRaw
        )
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
        if apiContentType == .news,
           let name = newsMetadata?.aggregator?.name,
           !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return name
        }
        return apiContentType?.displayName ?? "Article"
    }
    
    var articleMetadata: ArticleMetadata? {
        decodedPayloads.articleMetadata
    }

    var podcastMetadata: PodcastMetadata? {
        decodedPayloads.podcastMetadata
    }

    var newsMetadata: NewsMetadata? {
        decodedPayloads.newsMetadata
    }

    var sourceMetadata: SourceMetadata? {
        if let articleMetadata = articleMetadata?.sourceMetadata, articleMetadata.isDisplayable {
            return articleMetadata
        }
        if let newsMetadata = newsMetadata?.sourceMetadata, newsMetadata.isDisplayable {
            return newsMetadata
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
        guard apiContentType == .news,
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
        if apiContentType == .news {
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
        decodedPayloads.longformArtifact
    }

    /// Parse the raw summary as InterleavedSummary (returns nil if not interleaved format)
    var interleavedSummary: InterleavedSummary? {
        decodedPayloads.interleavedSummary
    }

    /// Parse the raw summary as InterleavedSummaryV2 (returns nil if not v2 format)
    var interleavedSummaryV2: InterleavedSummaryV2? {
        decodedPayloads.interleavedSummaryV2
    }

    /// Parse the raw summary as BulletedSummary (returns nil if not bulleted format)
    var bulletedSummary: BulletedSummary? {
        decodedPayloads.bulletedSummary
    }

    /// Parse the raw summary as EditorialNarrativeSummary (returns nil if not editorial format)
    var editorialSummary: EditorialNarrativeSummary? {
        decodedPayloads.editorialSummary
    }

    /// Parse the raw summary as StructuredSummary (returns nil if interleaved format)
    var structuredSummary: StructuredSummary? {
        decodedPayloads.structuredSummary
    }
}
