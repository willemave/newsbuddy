import XCTest
@testable import newsly

@MainActor
final class BadgeStatsStoreTests: XCTestCase {
    func testSimultaneousRefreshesShareOneRequest() async {
        let source = BlockingBadgeStatsSource(response: makeBadgeStats(article: 2))
        let scheduler = RecordingBadgeStatsRefreshScheduler()
        let store = BadgeStatsStore(
            fetchStats: { try await source.fetch() },
            scheduler: scheduler,
            notificationCenter: NotificationCenter()
        )

        store.activate()
        let firstRefresh = Task { await store.refreshStats() }
        let didStartFirstRefresh = await waitForBadgeCondition { source.callCount == 1 }
        XCTAssertTrue(didStartFirstRefresh)

        var secondRefreshStarted = false
        let secondRefresh = Task {
            secondRefreshStarted = true
            await store.refreshStats()
        }
        let didStartSecondRefresh = await waitForBadgeCondition { secondRefreshStarted }
        XCTAssertTrue(didStartSecondRefresh)
        XCTAssertEqual(source.callCount, 1)

        source.resume()
        await firstRefresh.value
        await secondRefresh.value

        XCTAssertEqual(source.callCount, 1)
        XCTAssertEqual(store.longFormCount, 2)
    }

    func testSuspendStopsRefreshesAndActivateRefreshes() async {
        let notificationCenter = NotificationCenter()
        let scheduler = RecordingBadgeStatsRefreshScheduler()
        let source = SequencedBadgeStatsSource(responses: [
            makeBadgeStats(article: 2, processing: 1),
            makeBadgeStats(article: 4),
        ])
        let store = BadgeStatsStore(
            fetchStats: { try await source.fetch() },
            scheduler: scheduler,
            notificationCenter: notificationCenter
        )

        store.activate()
        await store.refreshStats()
        XCTAssertEqual(source.callCount, 1)
        XCTAssertEqual(scheduler.scheduledIntervals, [5])
        XCTAssertTrue(scheduler.hasScheduledRefresh)

        store.suspend()
        XCTAssertFalse(scheduler.hasScheduledRefresh)

        await store.refreshStats()
        XCTAssertEqual(source.callCount, 1, "Suspended stores must not issue requests")

        store.activate()
        let didRefreshOnForeground = await waitForBadgeCondition { source.callCount == 2 }
        XCTAssertTrue(didRefreshOnForeground)
        XCTAssertEqual(store.longFormCount, 4)
        XCTAssertFalse(scheduler.hasScheduledRefresh)
    }

    func testStoreStartsSuspendedAndWaitsForActivation() async {
        let source = SequencedBadgeStatsSource(responses: [makeBadgeStats(article: 6)])
        let store = BadgeStatsStore(
            fetchStats: { try await source.fetch() },
            scheduler: RecordingBadgeStatsRefreshScheduler(),
            notificationCenter: NotificationCenter()
        )

        await store.refreshStats()
        XCTAssertEqual(source.callCount, 0)

        store.activate()
        let didRefresh = await waitForBadgeCondition { source.callCount == 1 }

        XCTAssertTrue(didRefresh)
        XCTAssertEqual(store.longFormCount, 6)
    }

    func testAuthenticationResetClearsCountsAndScheduledRefresh() async {
        let notificationCenter = NotificationCenter()
        let scheduler = RecordingBadgeStatsRefreshScheduler()
        let source = SequencedBadgeStatsSource(
            responses: [makeBadgeStats(article: 3, podcast: 2, processing: 1)]
        )
        let store = BadgeStatsStore(
            fetchStats: { try await source.fetch() },
            scheduler: scheduler,
            notificationCenter: notificationCenter
        )

        store.activate()
        await store.refreshStats()
        XCTAssertEqual(store.longFormCount, 5)
        XCTAssertEqual(store.processingCount, 1)
        XCTAssertTrue(scheduler.hasScheduledRefresh)

        notificationCenter.post(name: .authDidLogOut, object: nil)
        let didResetCounts = await waitForBadgeCondition {
            store.longFormCount == 0 && store.processingCount == 0
        }
        XCTAssertTrue(didResetCounts)
        XCTAssertEqual(store.longFormProcessingCount, 0)
        XCTAssertFalse(scheduler.hasScheduledRefresh)
    }

    func testTransientFailureKeepsPollingWhileProcessingIsKnownActive() async {
        let scheduler = RecordingBadgeStatsRefreshScheduler()
        var responses: [Result<APIBadgeStatsResponse, Error>] = [
            .success(makeBadgeStats(article: 1, processing: 1)),
            .failure(BadgeStatsTestError.transient),
            .success(makeBadgeStats(article: 2)),
        ]
        let store = BadgeStatsStore(
            fetchStats: {
                try responses.removeFirst().get()
            },
            scheduler: scheduler,
            notificationCenter: NotificationCenter()
        )

        store.activate()
        await store.refreshStats()
        XCTAssertTrue(scheduler.hasScheduledRefresh)

        await scheduler.runScheduledRefresh()
        XCTAssertTrue(scheduler.hasScheduledRefresh)
        XCTAssertEqual(store.processingCount, 1)

        await scheduler.runScheduledRefresh()
        XCTAssertFalse(scheduler.hasScheduledRefresh)
        XCTAssertEqual(store.longFormCount, 2)
        XCTAssertEqual(store.processingCount, 0)
    }

    func testUnreadMutationHelpersClampAtZero() async {
        let store = BadgeStatsStore(
            fetchStats: { makeBadgeStats(article: 1, podcast: 1) },
            scheduler: RecordingBadgeStatsRefreshScheduler(),
            notificationCenter: NotificationCenter()
        )
        store.activate()
        await store.refreshStats()

        store.decrementArticleCount(by: 3)
        XCTAssertEqual(store.longFormCount, 1)
        store.decrementPodcastCount()
        XCTAssertEqual(store.longFormCount, 0)
        store.incrementPodcastCount(by: 2)

        XCTAssertEqual(store.longFormCount, 2)
    }
}

@MainActor
private final class RecordingBadgeStatsRefreshScheduler: BadgeStatsRefreshScheduling {
    private(set) var scheduledIntervals: [TimeInterval] = []
    private var scheduledAction: (@MainActor () async -> Void)?

    var hasScheduledRefresh: Bool {
        scheduledAction != nil
    }

    func scheduleRefresh(
        after interval: TimeInterval,
        action: @escaping @MainActor () async -> Void
    ) {
        scheduledIntervals.append(interval)
        scheduledAction = action
    }

    func cancelRefresh() {
        scheduledAction = nil
    }

    func runScheduledRefresh() async {
        let action = scheduledAction
        scheduledAction = nil
        await action?()
    }
}

private enum BadgeStatsTestError: Error {
    case transient
}

@MainActor
private final class BlockingBadgeStatsSource {
    private let response: APIBadgeStatsResponse
    private var continuation: CheckedContinuation<Void, Never>?
    private(set) var callCount = 0

    init(response: APIBadgeStatsResponse) {
        self.response = response
    }

    func fetch() async throws -> APIBadgeStatsResponse {
        callCount += 1
        await withCheckedContinuation { continuation = $0 }
        return response
    }

    func resume() {
        continuation?.resume()
        continuation = nil
    }
}

@MainActor
private final class SequencedBadgeStatsSource {
    private var responses: [APIBadgeStatsResponse]
    private(set) var callCount = 0

    init(responses: [APIBadgeStatsResponse]) {
        self.responses = responses
    }

    func fetch() async throws -> APIBadgeStatsResponse {
        callCount += 1
        return responses.removeFirst()
    }
}

@MainActor
private func waitForBadgeCondition(
    timeoutNanoseconds: UInt64 = 1_000_000_000,
    condition: @escaping @MainActor () -> Bool
) async -> Bool {
    let startedAt = ContinuousClock.now
    while !condition() {
        if ContinuousClock.now - startedAt >= .nanoseconds(Int64(timeoutNanoseconds)) {
            return false
        }
        await Task.yield()
    }
    return true
}

private func makeBadgeStats(
    article: Int = 0,
    podcast: Int = 0,
    processing: Int = 0
) -> APIBadgeStatsResponse {
    APIBadgeStatsResponse(
        unread: APIUnreadCountsResponse(article: article, podcast: podcast, news: 0),
        processing: APIProcessingCountResponse(
            processingCount: processing,
            longFormCount: processing,
            newsCount: 0,
            newsCrawlCount: 0
        )
    )
}
