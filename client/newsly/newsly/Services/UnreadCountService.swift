//
//  UnreadCountService.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "UnreadCountService")

typealias UnreadCountsResponse = APIUnreadCountsResponse

@MainActor
@Observable
final class UnreadCountService {
    static let shared = UnreadCountService()

    var articleCount: Int = 0
    var podcastCount: Int = 0
    var newsCount: Int = 0

    // Computed properties for convenience
    var longFormCount: Int {
        articleCount + podcastCount
    }

    var shortFormCount: Int {
        newsCount
    }

    @ObservationIgnored
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

    func setPeriodicRefreshSuspended(_ isSuspended: Bool) {
        badgeStatsCoordinator.setRefreshSuspended(isSuspended)
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

    func incrementArticleCount(by amount: Int) {
        guard amount > 0 else { return }
        articleCount += amount
    }
    
    func incrementPodcastCount() {
        podcastCount += 1
    }

    func incrementPodcastCount(by amount: Int) {
        guard amount > 0 else { return }
        podcastCount += amount
    }

    func incrementNewsCount() {
        newsCount += 1
    }

    func incrementNewsCount(by amount: Int) {
        guard amount > 0 else { return }
        newsCount += amount
    }
}
