//
//  UnreadCountService.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import Combine
import Foundation

struct UnreadCountsResponse: Codable {
    let article: Int
    let podcast: Int
    let news: Int
    let dailyNewsDigest: Int?

    enum CodingKeys: String, CodingKey {
        case article
        case podcast
        case news
        case dailyNewsDigest = "daily_news_digest"
    }
}

@MainActor
class UnreadCountService: ObservableObject {
    static let shared = UnreadCountService()

    @Published var articleCount: Int = 0
    @Published var podcastCount: Int = 0
    @Published var newsCount: Int = 0
    @Published var dailyNewsDigestCount: Int = 0

    // Computed properties for convenience
    var longFormCount: Int {
        articleCount + podcastCount
    }

    var shortFormCount: Int {
        newsCount
    }

    private let client = APIClient.shared
    private var refreshTimer: Timer?

    private init() {
        // Start periodic refresh
        startPeriodicRefresh()
    }

    deinit {
        refreshTimer?.invalidate()
    }

    func refreshCounts() async {
        do {
            let response: UnreadCountsResponse = try await client.request(APIEndpoints.unreadCounts)
            articleCount = response.article
            podcastCount = response.podcast
            newsCount = response.news
            dailyNewsDigestCount = response.dailyNewsDigest ?? 0
        } catch {
            print("Failed to fetch unread counts: \(error)")
        }
    }
    
    private func startPeriodicRefresh() {
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 30.0, repeats: true) { _ in
            Task {
                await self.refreshCounts()
            }
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

    func decrementDailyDigestCount(by amount: Int = 1) {
        guard amount > 0 else { return }
        dailyNewsDigestCount = max(dailyNewsDigestCount - amount, 0)
    }

    func incrementDailyDigestCount(by amount: Int = 1) {
        guard amount > 0 else { return }
        dailyNewsDigestCount += amount
    }
}
