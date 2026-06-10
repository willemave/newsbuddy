//
//  UnreadCountService.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "UnreadCountService")

struct UnreadCountsResponse: Codable {
    let article: Int
    let podcast: Int
    let news: Int

    enum CodingKeys: String, CodingKey {
        case article
        case podcast
        case news
    }
}

@MainActor
class UnreadCountService: ObservableObject {
    static let shared = UnreadCountService()

    @Published var articleCount: Int = 0
    @Published var podcastCount: Int = 0
    @Published var newsCount: Int = 0

    // Computed properties for convenience
    var longFormCount: Int {
        articleCount + podcastCount
    }

    var shortFormCount: Int {
        newsCount
    }

    private let badgeStatsCoordinator = BadgeStatsRefreshCoordinator.shared

    private init() {
        badgeStatsCoordinator.attachUnreadService(self)
    }

    func refreshCounts() async {
        logger.debug("Refreshing unread counts through combined badge stats endpoint")
        await badgeStatsCoordinator.refreshStats()
    }

    @discardableResult
    func applyCounts(_ response: UnreadCountsResponse) -> Bool {
        var didChange = false
        if articleCount != response.article {
            articleCount = response.article
            didChange = true
        }
        if podcastCount != response.podcast {
            podcastCount = response.podcast
            didChange = true
        }
        if newsCount != response.news {
            newsCount = response.news
            didChange = true
        }
        return didChange
    }
    
    func stopPeriodicRefresh(resetCounts: Bool = false) {
        badgeStatsCoordinator.stop(resetCounts: resetCounts)
        if resetCounts {
            applyCounts(UnreadCountsResponse(article: 0, podcast: 0, news: 0))
        }
    }
    
    func decrementArticleCount(by amount: Int = 1) {
        guard amount > 0 else { return }
        articleCount = max(articleCount - amount, 0)
    }
    
    func decrementPodcastCount(by amount: Int = 1) {
        guard amount > 0 else { return }
        podcastCount = max(podcastCount - amount, 0)
    }

    func decrementNewsCount(by amount: Int = 1) {
        guard amount > 0 else { return }
        newsCount = max(newsCount - amount, 0)
    }
    
    func incrementArticleCount() {
        articleCount += 1
    }
    
    func incrementPodcastCount() {
        podcastCount += 1
    }

    func incrementNewsCount() {
        newsCount += 1
    }
}
