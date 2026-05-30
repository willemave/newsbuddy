//
//  NewsMetadata.swift
//  newsly
//
//  Created by Assistant on 9/23/25.
//

import Foundation

struct NewsSummaryMetadata: Codable {
    let title: String?
    let articleURL: String?
    let keyPoints: [String]
    let summary: String?
    let classification: String?
    let summarizationDate: String?

    enum CodingKeys: String, CodingKey {
        case title
        case articleURL = "article_url"
        case keyPoints = "key_points"
        case summary
        case classification
        case summarizationDate = "summarization_date"
    }

    init(
        title: String? = nil,
        articleURL: String? = nil,
        keyPoints: [String] = [],
        summary: String? = nil,
        classification: String? = nil,
        summarizationDate: String? = nil
    ) {
        self.title = title
        self.articleURL = articleURL
        self.keyPoints = keyPoints
        self.summary = summary
        self.classification = classification
        self.summarizationDate = summarizationDate
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let title = try container.decodeIfPresent(String.self, forKey: .title)
        let articleURL = try container.decodeIfPresent(String.self, forKey: .articleURL)
        let keyPoints = try container.decodeIfPresent([String].self, forKey: .keyPoints) ?? []
        let summary = try container.decodeIfPresent(String.self, forKey: .summary)
        let classification = try container.decodeIfPresent(String.self, forKey: .classification)
        let summarizationDate = try container.decodeIfPresent(String.self, forKey: .summarizationDate)
        self.init(
            title: title,
            articleURL: articleURL,
            keyPoints: keyPoints,
            summary: summary,
            classification: classification,
            summarizationDate: summarizationDate
        )
    }
}

struct NewsArticleMetadata: Codable {
    let url: String?
    let title: String?
    let sourceDomain: String?

    enum CodingKeys: String, CodingKey {
        case url
        case title
        case sourceDomain = "source_domain"
    }
}

struct NewsAggregatorMetadata: Codable {
    let name: String?
    let title: String?
    let externalID: String?
    let metadata: [String: AnyCodable]?

    enum CodingKeys: String, CodingKey {
        case name
        case title
        case externalID = "external_id"
        case metadata
    }

    var summaryText: String? {
        metadata?["summary_text"]?.value as? String
    }

    var feedName: String? {
        metadata?["feed_name"]?.value as? String
    }

    var sourceName: String? {
        metadata?["source_name"]?.value as? String
    }

    var relatedLinks: [NewsRelatedLink] {
        guard let raw = metadata?["related_links"]?.value else { return [] }
        if let links = raw as? [[String: Any]] {
            return links.compactMap { NewsRelatedLink(dictionary: $0) }
        }
        if let links = raw as? [Any] {
            return links.compactMap { element in
                guard let dict = element as? [String: Any] else { return nil }
                return NewsRelatedLink(dictionary: dict)
            }
        }
        return []
    }
}

struct NewsMetadata: Codable {
    let source: String?
    let platform: String?
    let summary: NewsSummaryMetadata?
    let article: NewsArticleMetadata?
    let aggregator: NewsAggregatorMetadata?
    let discussionURL: String?
    let discoveryTime: String?
    let sourceMetadata: SourceMetadata?

    enum CodingKeys: String, CodingKey {
        case source
        case platform
        case summary
        case article
        case aggregator
        case discussionURL = "discussion_url"
        case discoveryTime = "discovery_time"
        case sourceMetadata = "source_metadata"
    }
}

struct NewsRelatedLink: Identifiable {
    let title: String?
    let url: String

    var id: String { url }

    init?(dictionary: [String: Any]) {
        guard let url = dictionary["url"] as? String else { return nil }
        self.url = url
        self.title = dictionary["title"] as? String
    }
}
