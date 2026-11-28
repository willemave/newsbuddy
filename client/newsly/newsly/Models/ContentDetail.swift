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

enum ContentSummaryPayload {
    case longformArtifact(LongformArtifactEnvelope)
    case interleavedV1(InterleavedSummary)
    case interleavedV2(InterleavedSummaryV2)
    case bulleted(BulletedSummary)
    case editorial(EditorialNarrativeSummary)
    case structured(StructuredSummary)
}

/// Decoded API payloads derived from metadata once, when `ContentDetail` is
/// decoded. Keeping this immutable preserves value-model semantics while avoiding
/// repeated JSON re-serialization from SwiftUI view bodies.
private struct ContentDetailDecodedPayloads {
    let articleMetadata: ArticleMetadata?
    let podcastMetadata: PodcastMetadata?
    let newsMetadata: NewsMetadata?
    let summaryPayload: ContentSummaryPayload?
    let interestingExternalLinks: [RelevantLink]
    let newsRelevantLinks: [RelevantLink]

    init(
        contentType: APIContentType,
        url: String,
        newsArticleURL: String?,
        newsDiscussionURL: String?,
        metadata: [String: AnyCodable],
        summaryKind: String?,
        summaryVersion: Int?,
        structuredSummaryRaw: [String: AnyCodable]?,
        longformArtifactRaw: [String: AnyCodable]?
    ) {
        articleMetadata = contentType == .article
            ? Self.decode(ArticleMetadata.self, from: metadata)
            : nil
        podcastMetadata = contentType == .podcast
            ? Self.decode(PodcastMetadata.self, from: metadata)
            : nil
        let decodedNewsMetadata = contentType == .news
            ? Self.decode(NewsMetadata.self, from: metadata)
            : nil
        newsMetadata = decodedNewsMetadata

        let resolvedSummaryKind = summaryKind ?? metadata["summary_kind"]?.value as? String
        let resolvedSummaryVersion = summaryVersion
            ?? (metadata["summary_version"]?.value as? Int)
            ?? (metadata["summary_version"]?.value as? Double).map(Int.init)
        let rawSummary = structuredSummaryRaw
        let rawArtifact = longformArtifactRaw ?? structuredSummaryRaw

        switch (resolvedSummaryKind, resolvedSummaryVersion) {
        case ("longform_artifact", _):
            summaryPayload = Self.decode(LongformArtifactEnvelope.self, from: rawArtifact)
                .map(ContentSummaryPayload.longformArtifact)
        case ("long_interleaved", 1):
            summaryPayload = Self.decode(InterleavedSummary.self, from: rawSummary)
                .map(ContentSummaryPayload.interleavedV1)
        case ("long_interleaved", 2):
            summaryPayload = Self.decode(InterleavedSummaryV2.self, from: rawSummary)
                .map(ContentSummaryPayload.interleavedV2)
        case ("long_bullets", 1):
            summaryPayload = Self.decode(BulletedSummary.self, from: rawSummary)
                .map(ContentSummaryPayload.bulleted)
        case ("long_editorial_narrative", 1), ("long_editorial_narrative", 2):
            summaryPayload = Self.decode(EditorialNarrativeSummary.self, from: rawSummary)
                .map(ContentSummaryPayload.editorial)
        case ("long_structured", _):
            summaryPayload = Self.decode(StructuredSummary.self, from: rawSummary)
                .map(ContentSummaryPayload.structured)
        default:
            summaryPayload = nil
        }

        interestingExternalLinks = Self.decodeInterestingExternalLinks(from: metadata)
        newsRelevantLinks = Self.decodeNewsRelevantLinks(
            contentType: contentType,
            url: url,
            newsArticleURL: newsArticleURL,
            newsDiscussionURL: newsDiscussionURL,
            newsMetadata: decodedNewsMetadata,
            metadata: metadata
        )
    }

    private static func decode<T: Decodable>(_ type: T.Type, from raw: [String: AnyCodable]?) -> T? {
        AnyCodableDecoding.decodeLenient(type, from: raw)
    }

    private static func decodeInterestingExternalLinks(
        from metadata: [String: AnyCodable]
    ) -> [RelevantLink] {
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

    private static func decodeNewsRelevantLinks(
        contentType: APIContentType,
        url: String,
        newsArticleURL: String?,
        newsDiscussionURL: String?,
        newsMetadata: NewsMetadata?,
        metadata: [String: AnyCodable]
    ) -> [RelevantLink] {
        guard contentType == .news,
              let rawLinks = metadata["relevant_links"]?.value as? [[String: Any]] else {
            return []
        }

        var excluded = Set([
            url.nonEmptyTrimmed?.lowercased(),
            newsArticleURL?.nonEmptyTrimmed?.lowercased(),
            newsDiscussionURL?.nonEmptyTrimmed?.lowercased(),
            newsMetadata?.discussionURL?.nonEmptyTrimmed?.lowercased(),
            newsMetadata?.article?.url?.nonEmptyTrimmed?.lowercased()
        ].compactMap { $0 })
        var result: [RelevantLink] = []

        for rawLink in rawLinks {
            guard let link = RelevantLink(metadata: rawLink),
                  let key = link.url.nonEmptyTrimmed?.lowercased(),
                  !excluded.contains(key) else {
                continue
            }
            excluded.insert(key)
            result.append(link)
        }
        return result
    }
}

struct ContentDetail: Identifiable {
    let id: Int
    let contentType: APIContentType
    let url: String
    let title: String?
    let displayTitle: String
    let source: String?
    let status: APIContentStatus
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

    init(api response: APIContentDetailResponse) {
        id = response.id
        contentType = response.contentType
        url = response.url
        title = response.title
        displayTitle = Self.resolveDisplayTitle(
            response.displayTitle,
            title: response.title,
            url: response.url
        )
        source = response.source
        status = response.status
        errorMessage = response.errorMessage
        retryCount = response.retryCount
        metadata = response.metadata
        createdAt = ServerDate.format(response.createdAt)
        updatedAt = response.updatedAt.map(ServerDate.format)
        processedAt = response.processedAt.map(ServerDate.format)
        checkedOutBy = response.checkedOutBy
        checkedOutAt = response.checkedOutAt.map(ServerDate.format)
        publicationDate = response.publicationDate.map(ServerDate.format)
        isRead = response.isRead
        isSavedToKnowledge = response.isSavedToKnowledge
        summary = response.summary
        shortSummary = response.shortSummary
        summaryKind = response.summaryKind?.rawValue
        summaryVersion = response.summaryVersion?.rawValue
        structuredSummaryRaw = response.structuredSummary
        longformArtifactRaw = response.longformArtifact
        artifactType = response.artifactType
        previewBullets = response.previewBullets
        reasonToRead = response.reasonToRead
        bulletPoints = response.bulletPoints.map {
            BulletPoint(text: $0.text, category: $0.category)
        }
        quotes = response.quotes.map {
            Quote(text: $0.text, context: $0.context, attribution: $0.attribution)
        }
        topics = response.topics
        fullMarkdown = response.fullMarkdown
        bodyAvailable = response.bodyAvailable
        bodyKind = response.bodyKind
        bodyFormat = response.bodyFormat
        imageUrl = response.imageUrl
        thumbnailUrl = response.thumbnailUrl
        newsArticleURL = response.newsArticleUrl
        newsDiscussionURL = response.newsDiscussionUrl
        newsKeyPoints = response.newsKeyPoints
        newsSummary = response.newsSummary
        detectedFeed = response.detectedFeed
        canSubscribe = response.canSubscribe

        decodedPayloads = ContentDetailDecodedPayloads(
            contentType: response.contentType,
            url: response.url,
            newsArticleURL: response.newsArticleUrl,
            newsDiscussionURL: response.newsDiscussionUrl,
            metadata: response.metadata,
            summaryKind: response.summaryKind?.rawValue,
            summaryVersion: response.summaryVersion?.rawValue,
            structuredSummaryRaw: response.structuredSummary,
            longformArtifactRaw: response.longformArtifact
        )
    }

    init(api response: APINewsItemDetailResponse) {
        id = response.id
        contentType = response.contentType
        url = response.url
        title = response.title
        displayTitle = Self.resolveDisplayTitle(
            response.displayTitle,
            title: response.title,
            url: response.url
        )
        source = response.source
        status = response.status
        errorMessage = nil
        retryCount = response.retryCount
        metadata = response.metadata
        createdAt = ServerDate.format(response.createdAt)
        updatedAt = response.updatedAt.map(ServerDate.format)
        processedAt = response.processedAt.map(ServerDate.format)
        checkedOutBy = nil
        checkedOutAt = nil
        publicationDate = response.publicationDate.map(ServerDate.format)
        isRead = response.isRead
        isSavedToKnowledge = response.isSavedToKnowledge
        summary = response.summary
        shortSummary = response.shortSummary
        summaryKind = nil
        summaryVersion = nil
        structuredSummaryRaw = nil
        longformArtifactRaw = nil
        artifactType = nil
        previewBullets = nil
        reasonToRead = nil
        bulletPoints = []
        quotes = []
        topics = []
        fullMarkdown = nil
        bodyAvailable = response.bodyAvailable
        bodyKind = response.bodyKind
        bodyFormat = response.bodyFormat
        imageUrl = nil
        thumbnailUrl = nil
        newsArticleURL = response.newsArticleUrl
        newsDiscussionURL = response.newsDiscussionUrl
        newsKeyPoints = response.newsKeyPoints
        newsSummary = response.newsSummary
        detectedFeed = nil
        canSubscribe = response.canSubscribe

        decodedPayloads = ContentDetailDecodedPayloads(
            contentType: response.contentType,
            url: response.url,
            newsArticleURL: response.newsArticleUrl,
            newsDiscussionURL: response.newsDiscussionUrl,
            metadata: response.metadata,
            summaryKind: nil,
            summaryVersion: nil,
            structuredSummaryRaw: nil,
            longformArtifactRaw: nil
        )
    }

    private static func resolveDisplayTitle(
        _ displayTitle: String,
        title: String?,
        url: String
    ) -> String {
        if !displayTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return displayTitle
        }
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return title
        }
        return url
    }

    var primaryTimestamp: String {
        publicationDate ?? processedAt ?? createdAt
    }

    var detailTypeLabel: String {
        if contentType == .news,
           let name = newsMetadata?.aggregator?.name,
           !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return name
        }
        return contentType.displayName
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
        decodedPayloads.interestingExternalLinks
    }

    var newsRelevantLinks: [RelevantLink] {
        decodedPayloads.newsRelevantLinks
    }

    var relevantLinks: [RelevantLink] {
        if contentType == .news {
            return decodedPayloads.newsRelevantLinks
        }
        return decodedPayloads.interestingExternalLinks
    }

    // MARK: - Summary Type Detection

    var summaryPayload: ContentSummaryPayload? {
        decodedPayloads.summaryPayload
    }

    var longformArtifact: LongformArtifactEnvelope? {
        if case .longformArtifact(let value) = decodedPayloads.summaryPayload { return value }
        return nil
    }

    /// Parse the raw summary as InterleavedSummary (returns nil if not interleaved format)
    var interleavedSummary: InterleavedSummary? {
        if case .interleavedV1(let value) = decodedPayloads.summaryPayload { return value }
        return nil
    }

    /// Parse the raw summary as InterleavedSummaryV2 (returns nil if not v2 format)
    var interleavedSummaryV2: InterleavedSummaryV2? {
        if case .interleavedV2(let value) = decodedPayloads.summaryPayload { return value }
        return nil
    }

    /// Parse the raw summary as BulletedSummary (returns nil if not bulleted format)
    var bulletedSummary: BulletedSummary? {
        if case .bulleted(let value) = decodedPayloads.summaryPayload { return value }
        return nil
    }

    /// Parse the raw summary as EditorialNarrativeSummary (returns nil if not editorial format)
    var editorialSummary: EditorialNarrativeSummary? {
        if case .editorial(let value) = decodedPayloads.summaryPayload { return value }
        return nil
    }

    /// Parse the raw summary as StructuredSummary (returns nil if interleaved format)
    var structuredSummary: StructuredSummary? {
        if case .structured(let value) = decodedPayloads.summaryPayload { return value }
        return nil
    }
}
