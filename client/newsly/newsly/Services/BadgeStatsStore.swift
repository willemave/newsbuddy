//
//  BadgeStatsStore.swift
//  newsly
//

import Foundation
import Observation
import os.log
import UIKit

private let badgeStatsLogger = Logger(subsystem: "com.newsly", category: "BadgeStats")

@MainActor
protocol BadgeStatsRefreshScheduling: AnyObject {
    func scheduleRefresh(
        after interval: TimeInterval,
        action: @escaping @MainActor () async -> Void
    )
    func cancelRefresh()
}

@MainActor
private final class BadgeStatsRefreshScheduler: BadgeStatsRefreshScheduling {
    private var refreshTimer: Timer?

    deinit {
        refreshTimer?.invalidate()
    }

    func scheduleRefresh(
        after interval: TimeInterval,
        action: @escaping @MainActor () async -> Void
    ) {
        cancelRefresh()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: false) { _ in
            Task { @MainActor in
                await action()
            }
        }
    }

    func cancelRefresh() {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }
}

/// The single source of truth for unread and processing badge state.
///
/// The store coalesces callers onto one request, owns lifecycle-driven refreshes,
/// and only keeps a retry timer alive while server-side processing is active.
@MainActor
@Observable
final class BadgeStatsStore {
    typealias FetchStats = @MainActor () async throws -> APIBadgeStatsResponse

    static let shared = BadgeStatsStore()

    private var articleCount = 0
    private var podcastCount = 0
    private(set) var processingCount = 0
    private(set) var longFormProcessingCount = 0

    var longFormCount: Int {
        articleCount + podcastCount
    }

    @ObservationIgnored
    private let fetchStats: FetchStats
    @ObservationIgnored
    private let scheduler: any BadgeStatsRefreshScheduling
    @ObservationIgnored
    private let notificationCenter: NotificationCenter
    @ObservationIgnored
    private let isApplicationActive: @MainActor () -> Bool
    @ObservationIgnored
    private let refreshInterval: TimeInterval
    @ObservationIgnored
    private var refreshTask: Task<Void, Never>?
    @ObservationIgnored
    private var refreshGeneration = 0
    @ObservationIgnored
    private var lifecycleObservers: [NSObjectProtocol] = []
    @ObservationIgnored
    private var isRefreshSuspended: Bool

    init(
        fetchStats: @escaping FetchStats = {
            let response: APIBadgeStatsResponse = try await APIClient.shared.request(
                APIEndpoints.badgeStats
            )
            return response
        },
        scheduler: (any BadgeStatsRefreshScheduling)? = nil,
        notificationCenter: NotificationCenter = .default,
        isApplicationActive: @escaping @MainActor () -> Bool = {
            UIApplication.shared.applicationState == .active
        },
        refreshInterval: TimeInterval = 5
    ) {
        self.fetchStats = fetchStats
        self.scheduler = scheduler ?? BadgeStatsRefreshScheduler()
        self.notificationCenter = notificationCenter
        self.isApplicationActive = isApplicationActive
        self.refreshInterval = refreshInterval
        self.isRefreshSuspended = !isApplicationActive()
        installLifecycleObservers()
    }

    deinit {
        for observer in lifecycleObservers {
            notificationCenter.removeObserver(observer)
        }
    }

    func refreshStats() async {
        guard !isRefreshSuspended else { return }

        if let refreshTask {
            await refreshTask.value
            return
        }

        refreshGeneration += 1
        let generation = refreshGeneration
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.performRefreshStats()
        }
        refreshTask = task
        await task.value
        if refreshGeneration == generation {
            refreshTask = nil
        }
    }

    private func setRefreshSuspended(_ isSuspended: Bool) {
        guard isRefreshSuspended != isSuspended else { return }
        isRefreshSuspended = isSuspended

        if isSuspended {
            scheduler.cancelRefresh()
            cancelRefreshTask()
        }
    }

    private func stopAndReset() {
        scheduler.cancelRefresh()
        cancelRefreshTask()
        if articleCount != 0 { articleCount = 0 }
        if podcastCount != 0 { podcastCount = 0 }
        if processingCount != 0 { processingCount = 0 }
        if longFormProcessingCount != 0 { longFormProcessingCount = 0 }
    }

    private func applyUnreadCounts(_ response: APIUnreadCountsResponse) {
        if articleCount != response.article {
            articleCount = response.article
        }
        if podcastCount != response.podcast {
            podcastCount = response.podcast
        }
    }

    private func applyProcessingCounts(_ response: APIProcessingCountResponse) {
        if processingCount != response.processingCount {
            processingCount = response.processingCount
        }
        if longFormProcessingCount != response.longFormCount {
            longFormProcessingCount = response.longFormCount
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

    func incrementArticleCount(by amount: Int = 1) {
        guard amount > 0 else { return }
        articleCount += amount
    }

    func incrementPodcastCount(by amount: Int = 1) {
        guard amount > 0 else { return }
        podcastCount += amount
    }

    private func performRefreshStats() async {
        guard !Task.isCancelled, !isRefreshSuspended else { return }
        do {
            let response = try await fetchStats()
            guard !Task.isCancelled, !isRefreshSuspended else { return }
            applyUnreadCounts(response.unread)
            applyProcessingCounts(response.processing)
            scheduleNextRefresh(hasActiveProcessing: response.processing.processingCount > 0)
        } catch where Task.isCancelled || isRefreshSuspended {
            return
        } catch {
            badgeStatsLogger.error(
                "Failed to fetch badge stats: \(error.localizedDescription, privacy: .public)"
            )
            scheduleNextRefresh(hasActiveProcessing: processingCount > 0)
        }
    }

    private func scheduleNextRefresh(hasActiveProcessing: Bool) {
        scheduler.cancelRefresh()
        guard hasActiveProcessing, !isRefreshSuspended, isApplicationActive() else { return }
        scheduler.scheduleRefresh(after: refreshInterval) { [weak self] in
            await self?.refreshStats()
        }
    }

    private func installLifecycleObservers() {
        lifecycleObservers.append(notificationCenter.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.setRefreshSuspended(false)
                await self?.refreshStats()
            }
        })

        lifecycleObservers.append(notificationCenter.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.setRefreshSuspended(true)
            }
        })

        for notificationName in [Notification.Name.authDidLogOut, .authenticationRequired] {
            lifecycleObservers.append(notificationCenter.addObserver(
                forName: notificationName,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    self?.stopAndReset()
                }
            })
        }
    }

    private func cancelRefreshTask() {
        refreshGeneration += 1
        refreshTask?.cancel()
        refreshTask = nil
    }
}
