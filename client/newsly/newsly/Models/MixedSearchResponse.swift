//
//  MixedSearchResponse.swift
//  newsly
//

import Foundation

struct MixedSearchFeedResult: Codable, Identifiable {
    let id: String
    let title: String
    let siteURL: String
    let feedURL: String
    let feedType: String
    let feedFormat: String
    let description: String?
    let rationale: String?
    let evidenceURL: String?

    enum CodingKeys: String, CodingKey {
        case id
        case title
        case siteURL = "site_url"
        case feedURL = "feed_url"
        case feedType = "feed_type"
        case feedFormat = "feed_format"
        case description
        case rationale
        case evidenceURL = "evidence_url"
    }

    var previewURLString: String {
        evidenceURL ?? siteURL
    }
}

struct PodcastSearchResult: Codable, Identifiable {
    let title: String
    let episodeURL: String
    let podcastTitle: String?
    let source: String?
    let snippet: String?
    let feedURL: String?
    let publishedAt: String?
    let provider: String?
    let score: Double?

    var id: String { episodeURL }

    enum CodingKeys: String, CodingKey {
        case title
        case episodeURL = "episode_url"
        case podcastTitle = "podcast_title"
        case source
        case snippet
        case feedURL = "feed_url"
        case publishedAt = "published_at"
        case provider
        case score
    }
}

struct MixedSearchResponse: Codable {
    let query: String
    let content: [ContentSummary]
    let feeds: [MixedSearchFeedResult]
    let podcasts: [PodcastSearchResult]
}
