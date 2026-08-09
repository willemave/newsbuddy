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
    let isSubscribed: Bool

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
        case isSubscribed = "is_subscribed"
    }

    var previewURLString: String {
        evidenceURL ?? siteURL
    }

    init(api response: APIMixedSearchFeedResultResponse) {
        id = response.id
        title = response.title
        siteURL = response.siteUrl
        feedURL = response.feedUrl
        feedType = response.feedType
        feedFormat = response.feedFormat
        description = response.description
        rationale = response.rationale
        evidenceURL = response.evidenceUrl
        isSubscribed = response.isSubscribed
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

    init(api response: APIPodcastEpisodeSearchResultResponse) {
        title = response.title
        episodeURL = response.episodeUrl
        podcastTitle = response.podcastTitle
        source = response.source
        snippet = response.snippet
        feedURL = response.feedUrl
        publishedAt = response.publishedAt
        provider = response.provider
        score = response.score
    }
}

struct MixedSearchResponse: Codable {
    let query: String
    let content: [ContentSummary]
    let feeds: [MixedSearchFeedResult]
    let podcasts: [PodcastSearchResult]

    init(from decoder: Decoder) throws {
        let response = try APIMixedSearchResponse(from: decoder)
        query = response.query
        content = response.content.map(ContentSummary.init(api:))
        feeds = response.feeds.map(MixedSearchFeedResult.init(api:))
        podcasts = response.podcasts.map(PodcastSearchResult.init(api:))
    }
}
