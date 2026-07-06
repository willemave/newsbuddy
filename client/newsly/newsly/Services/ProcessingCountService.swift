//
//  ProcessingCountService.swift
//  newsly
//
//  Created by Assistant on 1/16/26.
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ProcessingCountService")

typealias ProcessingCountResponse = APIProcessingCountResponse

@MainActor
@Observable
final class ProcessingCountService {
    static let shared = ProcessingCountService()

    var processingCount: Int = 0
    var longFormProcessingCount: Int = 0
    var newsProcessingCount: Int = 0
    var newsCrawlCount: Int = 0

    @ObservationIgnored
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

    func setPeriodicRefreshSuspended(_ isSuspended: Bool) {
        badgeStatsCoordinator.setRefreshSuspended(isSuspended)
    }
}
