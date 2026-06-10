//
//  BadgeStatsRefreshCoordinator.swift
//  newsly
//

import Foundation
import os.log
import UIKit

private let badgeStatsLogger = Logger(subsystem: "com.newsly", category: "BadgeStats")

struct BadgeStatsResponse: Codable {
    let unread: UnreadCountsResponse
    let processing: ProcessingCountResponse
}

@MainActor
final class BadgeStatsRefreshCoordinator {
    static let shared = BadgeStatsRefreshCoordinator()

    private let client = APIClient.shared
    private weak var unreadService: UnreadCountService?
    private weak var processingService: ProcessingCountService?
    private var refreshTimer: Timer?
    private var observers: [NSObjectProtocol] = []
    private var didInstallLifecycleObservers = false

    private init() {}

    func attachUnreadService(_ service: UnreadCountService) {
        unreadService = service
        installLifecycleObserversIfNeeded()
    }

    func attachProcessingService(_ service: ProcessingCountService) {
        processingService = service
        installLifecycleObserversIfNeeded()
    }

    func refreshStats() async {
        do {
            let response: BadgeStatsResponse = try await client.request(APIEndpoints.badgeStats)
            unreadService?.applyCounts(response.unread)
            processingService?.applyCount(response.processing)
            scheduleNextRefresh(hasActiveProcessing: response.processing.processingCount > 0)
        } catch {
            badgeStatsLogger.error("Failed to fetch badge stats: \(error.localizedDescription, privacy: .public)")
            scheduleNextRefresh(hasActiveProcessing: false)
        }
    }

    func stop(resetCounts: Bool = false) {
        refreshTimer?.invalidate()
        refreshTimer = nil
        if resetCounts {
            unreadService?.applyCounts(UnreadCountsResponse(article: 0, podcast: 0, news: 0))
            processingService?.applyCount(
                ProcessingCountResponse(
                    processingCount: 0,
                    longFormCount: 0,
                    newsCount: 0,
                    newsCrawlCount: 0
                )
            )
        }
    }

    private func installLifecycleObserversIfNeeded() {
        guard !didInstallLifecycleObservers else { return }
        didInstallLifecycleObservers = true

        observers.append(NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                await self?.refreshStats()
            }
        })

        observers.append(NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.stop(resetCounts: false)
            }
        })

        for notificationName in [Notification.Name.authDidLogOut, .authenticationRequired] {
            observers.append(NotificationCenter.default.addObserver(
                forName: notificationName,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    self?.stop(resetCounts: true)
                }
            })
        }
    }

    private func scheduleNextRefresh(hasActiveProcessing: Bool) {
        refreshTimer?.invalidate()
        refreshTimer = nil
        guard hasActiveProcessing, UIApplication.shared.applicationState == .active else { return }
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: false) { [weak self] _ in
            Task { @MainActor in
                await self?.refreshStats()
            }
        }
    }
}
