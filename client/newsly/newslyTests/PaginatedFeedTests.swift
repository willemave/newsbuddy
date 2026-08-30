import XCTest
@testable import newsly

@MainActor
final class PaginatedFeedTests: XCTestCase {
    func testLoadInitialStoresItemsAndPagination() async {
        let feed = PaginatedFeed<TestFeedItem> { cursor in
            XCTAssertNil(cursor)
            return Page(
                items: [TestFeedItem(id: 1), TestFeedItem(id: 2)],
                nextCursor: "next",
                hasMore: true
            )
        }

        await feed.loadInitial()

        XCTAssertEqual(feed.items.map(\.id), [1, 2])
        XCTAssertEqual(feed.nextCursor, "next")
        XCTAssertTrue(feed.hasMore)
        XCTAssertEqual(feed.phase, .loaded)
    }

    func testSuccessfulEmptyLoadUsesEmptyPhase() async {
        let feed = PaginatedFeed<TestFeedItem> { _ in
            Page(items: [], nextCursor: nil, hasMore: false)
        }

        await feed.loadInitial()

        XCTAssertTrue(feed.items.isEmpty)
        XCTAssertEqual(feed.phase, .empty)
        XCTAssertFalse(feed.isRequestInFlight)
    }

    func testLoadNextPageAppendsAndSkipsDuplicateIDs() async {
        let loader = SequencePageLoader([
            Page(items: [TestFeedItem(id: 1), TestFeedItem(id: 2)], nextCursor: "next", hasMore: true),
            Page(items: [TestFeedItem(id: 2), TestFeedItem(id: 3)], nextCursor: nil, hasMore: false),
        ])
        let feed = PaginatedFeed<TestFeedItem>(loadPage: loader.loadPage)

        await feed.loadInitial()
        await feed.loadNextPage()

        XCTAssertEqual(feed.items.map(\.id), [1, 2, 3])
        XCTAssertNil(feed.nextCursor)
        XCTAssertFalse(feed.hasMore)
        XCTAssertEqual(feed.phase, .loaded)
    }

    func testRefreshInBackgroundMergesNewItemsOnTopAndKeepsExistingOrder() async {
        let feed = PaginatedFeed<TestFeedItem>(
            items: [TestFeedItem(id: 2), TestFeedItem(id: 1)],
            phase: .loaded,
            loadPage: { _ in
                Page(
                    items: [
                        TestFeedItem(id: 3),
                        TestFeedItem(id: 1),
                        TestFeedItem(id: 2),
                    ],
                    nextCursor: nil,
                    hasMore: false
                )
            },
            mergeReplacement: PaginatedFeed.mergeNewItemsOnTopKeepingExistingOrder
        )

        await feed.refreshInBackground()

        XCTAssertEqual(feed.items.map(\.id), [3, 2, 1])
        XCTAssertEqual(feed.phase, .loaded)
    }

    func testSupersededRequestDoesNotOverwriteNewerResult() async {
        let loader = ControlledPageLoader()
        let feed = PaginatedFeed<TestFeedItem>(loadPage: loader.loadPage)

        let firstLoad = Task { await feed.loadInitial() }
        await loader.waitForPendingRequestCount(1)

        let secondLoad = Task { await feed.loadInitial() }
        await loader.waitForPendingRequestCount(2)

        loader.resolveRequest(
            at: 1,
            with: Page(items: [TestFeedItem(id: 2)], nextCursor: nil, hasMore: false)
        )
        await secondLoad.value
        XCTAssertEqual(feed.items.map(\.id), [2])

        loader.resolveRequest(
            at: 0,
            with: Page(items: [TestFeedItem(id: 1)], nextCursor: nil, hasMore: false)
        )
        await firstLoad.value

        XCTAssertEqual(feed.items.map(\.id), [2])
        XCTAssertEqual(feed.phase, .loaded)
    }

    func testSupersededFailureDoesNotOverwriteNewerResult() async {
        let loader = ControlledResultPageLoader()
        let feed = PaginatedFeed<TestFeedItem>(loadPage: loader.loadPage)

        let firstLoad = Task { await feed.loadInitial() }
        await loader.waitForPendingRequestCount(1)

        let secondLoad = Task { await feed.loadInitial() }
        await loader.waitForPendingRequestCount(2)

        loader.resolveRequest(
            at: 1,
            with: .success(
                Page(items: [TestFeedItem(id: 2)], nextCursor: nil, hasMore: false)
            )
        )
        await secondLoad.value

        loader.resolveRequest(at: 0, with: .failure(TestPageError.failed))
        await firstLoad.value

        XCTAssertEqual(feed.items.map(\.id), [2])
        XCTAssertEqual(feed.phase, .loaded)
        XCTAssertFalse(feed.isRequestInFlight)
    }

    func testLoadNextPageIgnoresDuplicateTriggerWhileLoading() async {
        let loader = ControlledPageLoader()
        let feed = PaginatedFeed<TestFeedItem>(
            items: [TestFeedItem(id: 1)],
            phase: .loaded,
            nextCursor: "next",
            hasMore: true,
            loadPage: loader.loadPage
        )

        let firstLoadMore = Task { await feed.loadNextPage() }
        await loader.waitForPendingRequestCount(1)

        await feed.loadNextPage()

        XCTAssertEqual(loader.pendingRequestCount, 1)

        loader.resolveRequest(
            at: 0,
            with: Page(items: [TestFeedItem(id: 2)], nextCursor: nil, hasMore: false)
        )
        await firstLoadMore.value

        XCTAssertEqual(feed.items.map(\.id), [1, 2])
    }

    func testResetClearsItemsAndPaginationState() {
        let feed = PaginatedFeed<TestFeedItem>(
            items: [TestFeedItem(id: 1)],
            phase: .loaded,
            nextCursor: "next",
            hasMore: false,
            loadPage: { _ in Page(items: [], nextCursor: nil, hasMore: false) }
        )

        feed.reset()

        XCTAssertEqual(feed.items, [])
        XCTAssertNil(feed.nextCursor)
        XCTAssertTrue(feed.hasMore)
        XCTAssertEqual(feed.phase, .idle)
    }

    func testResetCancelsUnderlyingPageRequest() async {
        let loader = CancellablePageLoader()
        let feed = PaginatedFeed<TestFeedItem>(loadPage: loader.loadPage)
        let load = Task { await feed.loadInitial() }
        await loader.waitUntilStarted()

        feed.reset()
        await load.value

        XCTAssertTrue(loader.wasCancelled)
        XCTAssertEqual(feed.items, [])
        XCTAssertEqual(feed.phase, .idle)
    }

    func testCallerCancellationCancelsUnderlyingPageRequest() async {
        let loader = CancellablePageLoader(sleepDuration: .milliseconds(200))
        let feed = PaginatedFeed<TestFeedItem>(loadPage: loader.loadPage)
        let load = Task { await feed.loadInitial() }
        await loader.waitUntilStarted()

        load.cancel()
        await load.value

        XCTAssertTrue(loader.wasCancelled)
        XCTAssertEqual(feed.items, [])
        XCTAssertEqual(feed.phase, .idle)
    }

    func testCancelRequestRetainsItemsAndFencesLateResult() async {
        let loader = ControlledPageLoader()
        let feed = PaginatedFeed<TestFeedItem>(
            items: [TestFeedItem(id: 1)],
            phase: .loaded,
            loadPage: loader.loadPage
        )

        let obsolete = Task { await feed.refresh() }
        await loader.waitForPendingRequestCount(1)
        feed.cancelRequestRetainingState()

        XCTAssertEqual(feed.items.map(\.id), [1])
        XCTAssertEqual(feed.phase, .loaded)
        XCTAssertFalse(feed.isRequestInFlight)

        let replacement = Task { await feed.refresh() }
        await loader.waitForPendingRequestCount(2)
        loader.resolveRequest(
            at: 1,
            with: Page(items: [TestFeedItem(id: 2)], nextCursor: nil, hasMore: false)
        )
        await replacement.value

        loader.resolveRequest(
            at: 0,
            with: Page(items: [TestFeedItem(id: 3)], nextCursor: nil, hasMore: false)
        )
        await obsolete.value

        XCTAssertEqual(feed.items.map(\.id), [2])
        XCTAssertEqual(feed.phase, .loaded)
        XCTAssertFalse(feed.isRequestInFlight)
    }

    func testNestedCancellationKeepsLoadedItemsAndPagination() async {
        let feed = PaginatedFeed<TestFeedItem>(
            items: [TestFeedItem(id: 1)],
            phase: .loaded,
            nextCursor: "next",
            hasMore: true,
            loadPage: { _ in
                throw AuthError.networkError(URLError(.cancelled))
            }
        )

        await feed.refresh()

        XCTAssertEqual(feed.items.map(\.id), [1])
        XCTAssertEqual(feed.nextCursor, "next")
        XCTAssertTrue(feed.hasMore)
        XCTAssertEqual(feed.phase, .loaded)
        XCTAssertFalse(feed.isRequestInFlight)
    }
}

private struct TestFeedItem: Identifiable, Equatable, Sendable {
    let id: Int
}

@MainActor
private final class SequencePageLoader {
    private var pages: [Page<TestFeedItem>]

    init(_ pages: [Page<TestFeedItem>]) {
        self.pages = pages
    }

    func loadPage(cursor: String?) async throws -> Page<TestFeedItem> {
        guard !pages.isEmpty else {
            XCTFail("Unexpected page request for cursor \(cursor ?? "nil")")
            return Page(items: [], nextCursor: nil, hasMore: false)
        }
        return pages.removeFirst()
    }
}

@MainActor
private final class ControlledPageLoader {
    private struct PendingRequest {
        let cursor: String?
        let continuation: CheckedContinuation<Page<TestFeedItem>, Never>
    }

    private var pendingRequests: [PendingRequest] = []

    var pendingRequestCount: Int {
        pendingRequests.count
    }

    func loadPage(cursor: String?) async throws -> Page<TestFeedItem> {
        await withCheckedContinuation { continuation in
            pendingRequests.append(
                PendingRequest(cursor: cursor, continuation: continuation)
            )
        }
    }

    func resolveRequest(at index: Int, with page: Page<TestFeedItem>) {
        let request = pendingRequests.remove(at: index)
        request.continuation.resume(returning: page)
    }

    func waitForPendingRequestCount(
        _ expectedCount: Int,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if pendingRequests.count == expectedCount {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertEqual(pendingRequests.count, expectedCount, file: file, line: line)
    }
}

private enum TestPageError: Error {
    case failed
}

@MainActor
private final class ControlledResultPageLoader {
    private struct PendingRequest {
        let continuation: CheckedContinuation<Page<TestFeedItem>, Error>
    }

    private var pendingRequests: [PendingRequest] = []

    func loadPage(cursor _: String?) async throws -> Page<TestFeedItem> {
        try await withCheckedThrowingContinuation { continuation in
            pendingRequests.append(PendingRequest(continuation: continuation))
        }
    }

    func resolveRequest(
        at index: Int,
        with result: Result<Page<TestFeedItem>, Error>
    ) {
        let request = pendingRequests.remove(at: index)
        request.continuation.resume(with: result)
    }

    func waitForPendingRequestCount(
        _ expectedCount: Int,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if pendingRequests.count == expectedCount {
                return
            }
            try? await Task.sleep(for: .milliseconds(10))
        }

        XCTAssertEqual(pendingRequests.count, expectedCount, file: file, line: line)
    }
}

@MainActor
private final class CancellablePageLoader {
    private let sleepDuration: Duration
    private(set) var didStart = false
    private(set) var wasCancelled = false

    init(sleepDuration: Duration = .seconds(30)) {
        self.sleepDuration = sleepDuration
    }

    func loadPage(cursor: String?) async throws -> Page<TestFeedItem> {
        XCTAssertNil(cursor)
        didStart = true
        do {
            try await Task.sleep(for: sleepDuration)
        } catch is CancellationError {
            wasCancelled = true
            throw CancellationError()
        }
        return Page(items: [], nextCursor: nil, hasMore: false)
    }

    func waitUntilStarted(
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if didStart {
                return
            }
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(didStart, file: file, line: line)
    }
}
