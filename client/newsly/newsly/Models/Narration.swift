//
//  Narration.swift
//  newsly
//

import Foundation

enum NarrationTarget: Hashable {
    case audioEpisode(Int)
}

enum BriefingNarrationProgram {
    static let articles = "articles"
    static let podcasts = "podcasts"
    static let news = "news"

    static func scope(for key: String) -> APIBriefingNarrationScope? {
        switch key {
        case articles: .article_tier
        case podcasts: .podcast_tier
        case news: .news_program
        default: nil
        }
    }
}

struct NarrationPlaybackMetadata: Equatable {
    let title: String
    let collectionTitle: String
    let subtitle: String?
    let artworkURL: URL?
    let chapterIndex: Int
    let chapterCount: Int
}

typealias AudioEpisode = APIAudioEpisodeResponse
typealias BriefingNarration = APIBriefingNarrationResponse

typealias AudioEpisodeShareResponse = APIAudioEpisodeShareResponse

extension APIAudioEpisodeResponse: Identifiable {}

extension APIAudioEpisodeResponse {
    var isGenerating: Bool {
        status == .pending || status == .processing
    }

    var isCompleted: Bool {
        status == .completed
    }

    var isFailed: Bool {
        status == .failed
    }
}

extension APIBriefingNarrationResponse {
    var isGenerating: Bool {
        status == .pending || status == .processing
    }

    var firstPlayableChapter: AudioEpisode? {
        guard let first = chapters.first, first.isCompleted else { return nil }
        return first
    }

    var collectionTitle: String {
        switch scope {
        case .article_tier: "Articles"
        case .podcast_tier: "Podcasts"
        case .news_program: "News Briefing"
        case nil: title
        }
    }
}
