//
//  ProcessingCountService.swift
//  newsly
//
//  Created by Assistant on 1/16/26.
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ProcessingCountService")

struct ProcessingCountResponse: Codable {
    let processingCount: Int
    let longFormCount: Int
    let newsCount: Int
    let newsCrawlCount: Int

    enum CodingKeys: String, CodingKey {
        case processingCount = "processing_count"
        case longFormCount = "long_form_count"
        case newsCount = "news_count"
        case newsCrawlCount = "news_crawl_count"
    }
}

@MainActor
final class ProcessingCountService: ObservableObject {
    static let shared = ProcessingCountService()

    @Published var processingCount: Int = 0
    @Published var longFormProcessingCount: Int = 0
    @Published var newsProcessingCount: Int = 0
    @Published var newsCrawlCount: Int = 0

    private let badgeStatsCoordinator = BadgeStatsRefreshCoordinator.shared

    private init() {
        badgeStatsCoordinator.attachProcessingService(self)
    }

    func refreshCount() async {
        logger.debug("Refreshing processing count through combined badge stats endpoint")
        await badgeStatsCoordinator.refreshStats()
    }

    @discardableResult
    func applyCount(_ response: ProcessingCountResponse) -> Bool {
        var didChange = false
        if processingCount != response.processingCount {
            processingCount = response.processingCount
            didChange = true
        }
        if longFormProcessingCount != response.longFormCount {
            longFormProcessingCount = response.longFormCount
            didChange = true
        }
        if newsProcessingCount != response.newsCount {
            newsProcessingCount = response.newsCount
            didChange = true
        }
        if newsCrawlCount != response.newsCrawlCount {
            newsCrawlCount = response.newsCrawlCount
            didChange = true
        }
        return didChange
    }

    func stopPeriodicRefresh(resetCounts: Bool = false) {
        badgeStatsCoordinator.stop(resetCounts: resetCounts)
        if resetCounts {
            applyCount(
                ProcessingCountResponse(
                    processingCount: 0,
                    longFormCount: 0,
                    newsCount: 0,
                    newsCrawlCount: 0
                )
            )
        }
    }
}
