import XCTest
@testable import newsly

@MainActor
final class BriefingViewModelRetentionTests: XCTestCase {
    func testRetirementReadResponseKeepsSelectedLensStable() async {
        let (service, viewModel, _) = await makeRetiredLensScenario()

        XCTAssertEqual(service.indexEtags.prefix(2), [nil, "etag-1"])
        XCTAssertEqual(
            service.fetchLensRequests.filter { $0.key == "today" }.map(\.cursor),
            [nil]
        )
        XCTAssertEqual(viewModel.index?.version, 2)
        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 0)
    }

    func testExpiredRetiredLensIsEvictedWithoutOffscreenFetchAndReloadsOnReturn() async {
        let (service, viewModel, scheduler) = await makeRetiredLensScenario()

        viewModel.selectLens(key: "later")
        scheduler.expire("today")

        XCTAssertNil(viewModel.lenses["today"])
        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 1)

        viewModel.selectLens(key: "today")
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [11]
        }

        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 2)
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 1)
    }

    func testInactiveBriefingDoesNotFetchExpiredLensUntilReactivated() async {
        let (service, viewModel, scheduler) = await makeRetiredLensScenario()

        viewModel.setActive(false)
        scheduler.expire("today")

        XCTAssertFalse(viewModel.isActive)
        XCTAssertNil(viewModel.lenses["today"])
        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 1)

        viewModel.setActive(true)
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [11]
        }

        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 2)
    }

    func testReturningBeforeRetentionExpiryKeepsRetiredLensDocument() async {
        let (service, viewModel, scheduler) = await makeRetiredLensScenario()

        viewModel.selectLens(key: "later")
        viewModel.selectLens(key: "today")
        scheduler.expire("today")

        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])
        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 1)
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 0)
    }

    func testReactivationClearsCompletedInactiveFailureForProtectedLens() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [makeLensSummary(key: "today", segmentCount: 2)]),
                etag: "etag-1"
            )
        ]
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                segments: [makeSegment(id: 10, sourceKeys: ["content:1"])],
                nextCursor: "today-page-2",
                hasMore: true
            )
        ]
        service.fetchLensWaitRequestIndices = [1]
        service.fetchLensErrorsByRequestIndex[1] = URLError(.networkConnectionLost)
        let viewModel = BriefingViewModel(service: service)

        viewModel.setActive(true)
        await waitForBriefingCondition {
            service.fetchLensRequests.count == 2
                && viewModel.selectedLens?.segments.map(\.id) == [10]
        }
        // Isolate the narrow race: read retirement protects the visible document
        // while the already-started continuation still owns failure publication.
        viewModel.mutateLensState("today") { state in
            state.staleness = .readRetirement
        }

        viewModel.setActive(false)
        XCTAssertTrue(viewModel.lensStates["today"]?.retainsReadRetirement == true)
        XCTAssertTrue(viewModel.isLensReplacementProtected("today"))
        XCTAssertTrue(viewModel.tasks.isRunning(.lens("today")))

        service.resumeLensRequest(at: 1)
        await waitForBriefingCondition {
            viewModel.lensContinuationErrors["today"] != nil
                && !viewModel.tasks.isRunning(.lens("today"))
        }
        XCTAssertFalse(viewModel.isActive)
        XCTAssertNotNil(viewModel.lensContinuationErrors["today"])

        viewModel.setActive(true)

        XCTAssertNil(viewModel.lensContinuationErrors["today"])
        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])
        XCTAssertEqual(service.fetchLensRequests.map(\.cursor), [nil, "today-page-2"])
    }

    func testRetirementArrivingAfterExpiryEvictsWithoutOffscreenFetch() async {
        let service = MockBriefingService()
        let scheduler = MockBriefingLensRetentionScheduler()
        let today = makeLensSummary(key: "today", position: 0)
        let later = makeLensSummary(key: "later", position: 1)
        let readSegment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [today, later]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [today, later]), etag: "etag-2")
        ]
        service.lensPageResponses["today"] = [
            makeLens(key: "today", version: 1, position: 0, segments: [readSegment]),
            makeLens(
                key: "today",
                version: 2,
                position: 0,
                segments: [makeSegment(id: 11, sourceKeys: ["news:2"])]
            )
        ]
        service.lensPageResponses["later"] = [
            makeUnrelatedLens(version: 1),
            makeUnrelatedLens(version: 2)
        ]
        service.markReadWaitsForResume = true
        service.readMarkResponse = APIBriefingReadMarkResponse(
            marked: 1,
            retired: 1,
            version: 2
        )
        let viewModel = BriefingViewModel(
            service: service,
            lensRetentionScheduler: scheduler
        )

        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens?.segments.map(\.id) == [10] }
        viewModel.markSegmentSeen(readSegment)
        await waitForBriefingCondition { service.markReadCalls.count == 1 }
        viewModel.selectLens(key: "later")
        scheduler.expire("today")
        service.resumeMarkRead()

        await waitForBriefingCondition(timeoutNanoseconds: 1_500_000_000) {
            viewModel.index?.version == 2 && viewModel.lenses["today"] == nil
        }

        XCTAssertEqual(viewModel.selectedLensKey, "later")
        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 1)

        viewModel.selectLens(key: "today")
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [11]
        }
        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 2)
    }

    private func makeRetiredLensScenario() async -> (
        MockBriefingService,
        BriefingViewModel,
        MockBriefingLensRetentionScheduler
    ) {
        let service = MockBriefingService()
        let scheduler = MockBriefingLensRetentionScheduler()
        let today = makeLensSummary(key: "today", position: 0)
        let later = makeLensSummary(key: "later", position: 1)
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [today, later]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [today, later]), etag: "etag-2"),
            .notModified
        ]
        let readSegment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.lensPageResponses["today"] = [
            makeLens(key: "today", version: 1, position: 0, segments: [readSegment]),
            makeLens(
                key: "today",
                version: 2,
                position: 0,
                segments: [makeSegment(id: 11, sourceKeys: ["news:2"])]
            )
        ]
        service.lensPageResponses["later"] = [
            makeUnrelatedLens(version: 1),
            makeUnrelatedLens(version: 2)
        ]
        service.readMarkResponse = APIBriefingReadMarkResponse(
            marked: 1,
            retired: 1,
            version: 2
        )
        let viewModel = BriefingViewModel(
            service: service,
            lensRetentionScheduler: scheduler
        )

        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens?.segments.map(\.id) == [10] }
        viewModel.markSegmentSeen(readSegment)
        await waitForBriefingCondition(timeoutNanoseconds: 1_500_000_000) {
            viewModel.index?.version == 2 && service.indexEtags.count == 2
        }
        return (service, viewModel, scheduler)
    }

    private func makeUnrelatedLens(version: Int) -> APIBriefingLensResponse {
        makeLens(
            key: "later",
            version: version,
            position: 1,
            segments: [makeSegment(id: 20, sourceKeys: ["news:9"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "news:9",
                    kind: "news",
                    id: 9,
                    title: "Unrelated",
                    read: false
                )
            ]
        )
    }
}
