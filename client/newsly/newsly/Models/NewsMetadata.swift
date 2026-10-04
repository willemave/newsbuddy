//
//  NewsMetadata.swift
//  newsly
//
//  Created by Assistant on 9/23/25.
//

import Foundation

struct NewsSummaryMetadata: Decodable {
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

struct NewsArticleMetadata: Decodable {
    let url: String?
    let title: String?
}

struct NewsAggregatorMetadata: Decodable {
    let name: String?
}

struct NewsMetadata: Decodable {
    let source: String?
    let platform: String?
    let summary: NewsSummaryMetadata?
    let article: NewsArticleMetadata?
    let aggregator: NewsAggregatorMetadata?
    let discussionURL: String?
    let sourceMetadata: SourceMetadata?

    enum CodingKeys: String, CodingKey {
        case source
        case platform
        case summary
        case article
        case aggregator
        case discussionURL = "discussion_url"
        case sourceMetadata = "source_metadata"
    }
}
