//
//  MixedSearchResponse.swift
//  newsly
//

import Foundation

typealias MixedSearchFeedResult = APIMixedSearchFeedResultResponse

extension APIMixedSearchFeedResultResponse: Identifiable {
    var siteURL: String { siteUrl }
    var feedURL: String { feedUrl }
    var evidenceURL: String? { evidenceUrl }
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

struct MixedSearchResponse {
    let query: String
    let content: [ContentSummary]
    let feeds: [MixedSearchFeedResult]
    let podcasts: [PodcastSearchResult]

    init(api response: APIMixedSearchResponse) {
        query = response.query
        content = response.content.map(ContentSummary.init(api:))
        feeds = response.feeds
        podcasts = response.podcasts.map(PodcastSearchResult.init(api:))
    }
}
