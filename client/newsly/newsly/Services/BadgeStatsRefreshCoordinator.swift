//
//  BadgeStatsRefreshCoordinator.swift
//  newsly
//

import Foundation
import os.log
import UIKit

private let badgeStatsLogger = Logger(subsystem: "com.newsly", category: "BadgeStats")

typealias BadgeStatsResponse = APIBadgeStatsResponse

@MainActor
final class BadgeStatsRefreshCoordinator {
    static let shared = BadgeStatsRefreshCoordinator()

    private let client = APIClient.shared
    private weak var unreadService: UnreadCountService?
    private weak var processingService: ProcessingCountService?
    private var refreshTask: Task<Void, Never>?
    private var refreshGeneration = 0
    private var refreshTimer: Timer?
    private var observers: [NSObjectProtocol] = []
    private var didInstallLifecycleObservers = false
    private var isRefreshSuspended = false

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
        guard !isRefreshSuspended else { return }

        if let refreshTask {
            await refreshTask.value
            return
        }

        refreshGeneration += 1
        let generation = refreshGeneration
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performRefreshStats()
        }
        refreshTask = task
        await task.value
        if refreshGeneration == generation {
            refreshTask = nil
        }
    }

    private func performRefreshStats() async {
        guard !Task.isCancelled, !isRefreshSuspended else { return }
        do {
            let response: BadgeStatsResponse = try await client.request(APIEndpoints.badgeStats)
            guard !Task.isCancelled, !isRefreshSuspended else { return }
            unreadService?.applyCounts(response.unread)
            processingService?.applyCount(response.processing)
            scheduleNextRefresh(hasActiveProcessing: response.processing.processingCount > 0)
        } catch where Task.isCancelled || isRefreshSuspended {
            return
        } catch {
            badgeStatsLogger.error("Failed to fetch badge stats: \(error.localizedDescription, privacy: .public)")
            scheduleNextRefresh(hasActiveProcessing: false)
        }
    }

    func setRefreshSuspended(_ isSuspended: Bool) {
        guard isRefreshSuspended != isSuspended else { return }
        isRefreshSuspended = isSuspended

        if isSuspended {
            refreshTimer?.invalidate()
            refreshTimer = nil
            cancelRefreshTask()
        }
    }

    func stop(resetCounts: Bool = false) {
        refreshTimer?.invalidate()
        refreshTimer = nil
        cancelRefreshTask()
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
                self?.setRefreshSuspended(false)
                await self?.refreshStats()
            }
        })

        observers.append(NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.setRefreshSuspended(true)
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

    private func cancelRefreshTask() {
        refreshGeneration += 1
        refreshTask?.cancel()
        refreshTask = nil
    }

    private func scheduleNextRefresh(hasActiveProcessing: Bool) {
        refreshTimer?.invalidate()
        refreshTimer = nil
        guard hasActiveProcessing, !isRefreshSuspended, UIApplication.shared.applicationState == .active else { return }
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: false) { [weak self] _ in
            Task { @MainActor in
                await self?.refreshStats()
            }
        }
    }
}
