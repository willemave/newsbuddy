import XCTest
@testable import newsly

@MainActor
final class BriefingViewModelRefreshTests: XCTestCase {
    func testPullToRefreshFlushesPendingReadMarksBeforeForceLoad() async {
        let service = MockBriefingService()
        let segment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 1, segments: [segment])
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 1, retired: 0, version: 2)
        let viewModel = BriefingViewModel(service: service)

        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens?.segments.first?.id == 10 }
        viewModel.markSegmentSeen(segment)

        service.lensResponses["today"] = makeLens(key: "today", version: 2, segments: [])
        await viewModel.pullToRefresh()
        await waitForBriefingCondition(timeoutNanoseconds: 2_500_000_000) {
            viewModel.selectedLens?.version == 2 && service.indexEtags.count == 2
        }

        XCTAssertEqual(service.markReadCalls, [["content:1"]])
        XCTAssertEqual(service.indexEtags, [nil, "etag-1"])
        let markReadIndex = service.events.firstIndex(of: "markRead:content:1")
        let refreshIndex = service.events.firstIndex(of: "requestRefresh")
        XCTAssertNotNil(markReadIndex)
        XCTAssertNotNil(refreshIndex)
        XCTAssertLessThan(markReadIndex!, refreshIndex!)
    }

    func testPullToRefreshContinuesWhenReadMarkFlushFails() async {
        let service = MockBriefingService()
        let segment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 1, segments: [segment])
        let viewModel = BriefingViewModel(service: service)

        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens?.segments.first?.id == 10 }
        viewModel.markSegmentSeen(segment)

        service.markReadError = URLError(.notConnectedToInternet)
        service.lensResponses["today"] = makeLens(key: "today", version: 2, segments: [])
        await viewModel.pullToRefresh()
        await waitForBriefingCondition { viewModel.index?.version == 2 }

        XCTAssertEqual(service.markReadCalls.first, ["content:1"])
        XCTAssertEqual(service.indexEtags, [nil, "etag-1"])
        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])
        let markReadIndex = service.events.firstIndex(of: "markRead:content:1")
        let refreshIndex = service.events.firstIndex(of: "requestRefresh")
        XCTAssertNotNil(markReadIndex)
        XCTAssertNotNil(refreshIndex)
        XCTAssertLessThan(markReadIndex!, refreshIndex!)
    }

    func testWrappedCancelledManualRefreshKeepsReadyContent() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "etag-1")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)
        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        service.refreshError = APIError.networkError(URLError(.cancelled))

        await viewModel.pullToRefresh()

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertEqual(viewModel.refreshPhase, .idle)
        XCTAssertNotNil(viewModel.selectedLens)
    }

    func testGenuineManualRefreshFailureIsActionLevelOnly() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "etag-1")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)
        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        service.refreshError = URLError(.notConnectedToInternet)

        await viewModel.pullToRefresh()

        XCTAssertEqual(viewModel.state, .loaded)
        guard case .failed = viewModel.refreshPhase else {
            return XCTFail("Expected an action-level refresh failure")
        }
        XCTAssertNotNil(viewModel.selectedLens)
    }

    func testDuplicateManualRefreshDoesNotDuplicatePost() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .notModified,
            .notModified,
            .notModified
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        service.refreshDelayNanoseconds = 100_000_000
        let viewModel = BriefingViewModel(service: service)
        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        let first = Task { await viewModel.pullToRefresh() }
        await waitForBriefingCondition { service.refreshRequestCount == 1 }
        await viewModel.pullToRefresh()
        await first.value

        XCTAssertEqual(service.refreshRequestCount, 1)
    }

    func testManualRefreshPollsPastOldDelayWithoutReplacingSelectedLens() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 1)
        let viewModel = BriefingViewModel(
            service: service,
            refreshPollDelays: [400_000_000]
        )
        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens?.version == 1 }
        service.lensResponses["today"] = makeLens(key: "today", version: 2)

        await viewModel.pullToRefresh()
        XCTAssertEqual(viewModel.refreshPhase, .waitingForVersion)
        await waitForBriefingCondition(timeoutNanoseconds: 1_000_000_000) {
            viewModel.index?.version == 2
        }

        XCTAssertEqual(viewModel.refreshPhase, .idle)
        XCTAssertEqual(viewModel.selectedLens?.version, 1)
    }

    func testDeactivationDuringRefreshRequestDoesNotStartPolling() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        service.refreshWaitsForResume = true
        let viewModel = BriefingViewModel(service: service, refreshPollDelays: [1_000_000])
        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        let refresh = Task { await viewModel.pullToRefresh() }
        await waitForBriefingCondition { service.refreshRequestCount == 1 }
        viewModel.setActive(false)
        service.resumeRefreshRequest()
        await refresh.value
        try? await Task.sleep(nanoseconds: 20_000_000)

        XCTAssertEqual(viewModel.refreshPhase, .idle)
        XCTAssertEqual(service.refreshRequestCount, 1)
        XCTAssertEqual(service.indexEtags, [nil])
    }
}
