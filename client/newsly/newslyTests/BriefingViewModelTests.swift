import XCTest
@testable import newsly

@MainActor
final class BriefingViewModelTests: XCTestCase {
    func testActiveStateTracksBriefingLifecycle() {
        let viewModel = BriefingViewModel(service: MockBriefingService())

        XCTAssertFalse(viewModel.isActive)

        viewModel.setActive(true)
        XCTAssertTrue(viewModel.isActive)

        viewModel.setActive(false)
        XCTAssertFalse(viewModel.isActive)
    }

    func testDeactivationKeepsSelectedLensHydrationAndCancelsPrefetch() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [
                    makeLensSummary(key: "today", position: 0),
                    makeLensSummary(key: "later", position: 1),
                ]),
                etag: "etag-1"
            )
        ]
        service.lensResponses["today"] = makeLens(key: "today", position: 0)
        service.lensResponses["later"] = makeLens(key: "later", position: 1)
        service.fetchLensDelayNanoseconds = 150_000_000
        let viewModel = BriefingViewModel(service: service)

        viewModel.setActive(true)
        await waitForBriefingCondition { service.fetchLensKeys == ["today"] }
        viewModel.setActive(false)
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(service.fetchLensKeys, ["today"])
        XCTAssertNotNil(viewModel.lenses["today"])
        XCTAssertNil(viewModel.lenses["later"])
    }

    func testReactivationReplacesInFlightSelectedLensRequestBeforeItCanPublishFailure() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [makeLensSummary(key: "today", segmentCount: 2)]),
                etag: "etag-1"
            ),
            .notModified
        ]
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                segments: [makeSegment(id: 10, sourceKeys: ["content:1"])],
                nextCursor: "today-page-2",
                hasMore: true
            ),
            makeLens(
                key: "today",
                segments: [makeSegment(id: 9, sourceKeys: ["news:2"])]
            )
        ]
        service.fetchLensWaitRequestIndices = [1]
        service.fetchLensErrorsByRequestIndex[1] = URLError(.networkConnectionLost)
        service.fetchIndexWaitRequestIndices = [1]
        let viewModel = BriefingViewModel(
            service: service,
            indexFreshnessInterval: 0
        )

        viewModel.setActive(true)
        await waitForBriefingCondition { service.fetchLensRequests.count == 2 }
        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])

        viewModel.setActive(false)
        viewModel.setActive(true)
        await waitForBriefingCondition {
            service.fetchLensRequests.count == 3
                && viewModel.selectedLens?.segments.map(\.id) == [10, 9]
        }
        service.resumeLensRequest(at: 1)
        service.resumeIndexRequest(at: 1)
        try? await Task.sleep(nanoseconds: 20_000_000)

        XCTAssertEqual(
            service.fetchLensRequests.map(\.cursor),
            [nil, "today-page-2", "today-page-2"]
        )
        XCTAssertNil(viewModel.lensErrors["today"])
        XCTAssertNil(viewModel.lensContinuationErrors["today"])
    }

    func testFirstRunLoadsStartHereWithoutAReadableCategory() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [], firstRun: makeFirstRun()), etag: "first-run-1")
        ]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertTrue(viewModel.isStartHereSelected)
        XCTAssertEqual(viewModel.firstRun?.connectedSourceCount, 3)
        XCTAssertTrue(service.fetchLensKeys.isEmpty)
    }

    func testOpeningReadyCategoryOptimisticallyCompletesFirstRun() async {
        let service = MockBriefingService()
        let technology = makeLensSummary(key: "technology", title: "Technology")
        service.indexResults = [
            .value(
                makeIndex(
                    lenses: [technology],
                    firstRun: makeFirstRun(readyCategoryKeys: ["technology"])
                ),
                etag: "first-run-2"
            )
        ]
        service.lensResponses["technology"] = makeLens(key: "technology")
        let snapshotStore = MockBriefingSnapshotStore(userID: 1)
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()
        viewModel.selectLens(key: "technology")
        await waitForBriefingCondition { service.firstRunCompletionCount == 1 }
        await waitForBriefingCondition(timeoutNanoseconds: 1_500_000_000) {
            !snapshotStore.savedSnapshots.isEmpty
        }

        XCTAssertEqual(viewModel.selectedLensKey, "technology")
        XCTAssertNil(viewModel.firstRun)
        XCTAssertFalse(viewModel.isStartHereSelected)
        XCTAssertNil(snapshotStore.savedSnapshots.last?.index.firstRun)
    }

    func testOpeningReadyCategoryRetriesCompletionAfterFailure() async {
        let service = MockBriefingService()
        let technology = makeLensSummary(key: "technology", title: "Technology")
        service.indexResults = [
            .value(
                makeIndex(
                    lenses: [technology],
                    firstRun: makeFirstRun(readyCategoryKeys: ["technology"])
                ),
                etag: "first-run-retry"
            )
        ]
        service.lensResponses["technology"] = makeLens(key: "technology")
        service.firstRunCompletionFailuresRemaining = 1
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        viewModel.selectLens(key: "technology")
        await waitForBriefingCondition { service.firstRunCompletionCount == 2 }

        XCTAssertNil(viewModel.firstRun)
    }

    func testLoadIndexSelectsFirstLensAndLoadsIt() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "briefing-v1")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertEqual(viewModel.selectedLensKey, "today")
        XCTAssertEqual(service.fetchLensKeys, ["today"])
        XCTAssertEqual(viewModel.selectedLens?.lens.key, "today")
    }

    func testSelectedLensCompletesPaginationBeforeNeighborPrefetch() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(
                    lenses: [
                        makeLensSummary(key: "today", position: 0, segmentCount: 2),
                        makeLensSummary(key: "later", position: 1)
                    ]
                ),
                etag: nil
            )
        ]
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                segments: [makeSegment(id: 10, sourceKeys: ["content:1"])],
                nextCursor: "today-page-2",
                hasMore: true
            ),
            makeLens(
                key: "today",
                segments: [makeSegment(id: 9, sourceKeys: ["news:2"])]
            )
        ]
        service.lensResponses["later"] = makeLens(key: "later", position: 1)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { service.fetchLensRequests.count == 3 }

        XCTAssertEqual(service.fetchLensRequests.map(\.key), ["today", "today", "later"])
        XCTAssertNil(service.fetchLensRequests[0].cursor)
        XCTAssertEqual(service.fetchLensRequests[1].cursor, "today-page-2")
        XCTAssertNil(service.fetchLensRequests[2].cursor)
        XCTAssertEqual(viewModel.lenses["today"]?.segments.map(\.id), [10, 9])
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 0)
    }

    func testContinuationFailureKeepsFirstPageVisibleAndRetriesFromItsCursor() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [makeLensSummary(key: "today", segmentCount: 2)]),
                etag: nil
            )
        ]
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                segments: [makeSegment(id: 10, sourceKeys: ["content:1"])],
                nextCursor: "today-page-2",
                hasMore: true
            ),
            makeLens(
                key: "today",
                segments: [makeSegment(id: 9, sourceKeys: ["news:2"])]
            )
        ]
        service.fetchLensErrors = [nil, URLError(.notConnectedToInternet)]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition {
            viewModel.lensContinuationErrors["today"] != nil
        }

        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])
        XCTAssertEqual(viewModel.selectedLens?.nextCursor, "today-page-2")
        XCTAssertFalse(viewModel.lensContinuationLoadingKeys.contains("today"))
        guard let firstSegmentModel = viewModel.renderModel(for: "today")?.segments.first else {
            XCTFail("Expected the first page render model")
            return
        }

        viewModel.retryLens(key: "today")
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [10, 9]
        }

        XCTAssertEqual(service.fetchLensRequests.map(\.cursor), [nil, "today-page-2", "today-page-2"])
        XCTAssertNil(viewModel.lensContinuationErrors["today"])
        XCTAssertFalse(viewModel.selectedLens?.hasMore ?? true)
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 0)
        guard let appendedFirstSegmentModel = viewModel.renderModel(for: "today")?.segments.first else {
            XCTFail("Expected the appended render model")
            return
        }
        XCTAssertTrue(firstSegmentModel === appendedFirstSegmentModel)
    }

    func testStaleContinuationRetryRestartsFromFirstPage() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [makeLensSummary(key: "today", segmentCount: 2)]),
                etag: nil
            )
        ]
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                version: 1,
                segments: [makeSegment(id: 10)],
                nextCursor: "stale-page-2",
                hasMore: true
            ),
            makeLens(
                key: "today",
                version: 1,
                segments: [makeSegment(id: 20)]
            )
        ]
        service.fetchLensErrors = [nil, BriefingLensFetchError.staleCursor, nil]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.lensContinuationErrors["today"] != nil }

        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])

        viewModel.retryLens(key: "today")
        await waitForBriefingCondition { viewModel.selectedLens?.segments.map(\.id) == [20] }

        XCTAssertEqual(service.fetchLensRequests.map(\.cursor), [nil, "stale-page-2", nil])
        XCTAssertNil(viewModel.lensContinuationErrors["today"])
        XCTAssertEqual(viewModel.selectedLens?.version, 1)
    }

    func testStaleReplacementFailureKeepsVisibleLensAndRetriesFromFirstPage() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "v1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "v2")
        ]
        service.lensPageResponses["today"] = [
            makeLens(key: "today", version: 1, segments: [makeSegment(id: 10)]),
            makeLens(key: "today", version: 2, segments: [makeSegment(id: 20)])
        ]
        service.fetchLensErrors = [nil, URLError(.notConnectedToInternet), nil]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens?.segments.map(\.id) == [10] }
        await viewModel.refreshIndex()
        await waitForBriefingCondition { viewModel.lensContinuationErrors["today"] != nil }

        XCTAssertEqual(viewModel.selectedLens?.segments.map(\.id), [10])

        viewModel.retryLens(key: "today")
        await waitForBriefingCondition { viewModel.selectedLens?.segments.map(\.id) == [20] }

        XCTAssertEqual(service.fetchLensRequests.map(\.cursor), [nil, nil, nil])
        XCTAssertNil(viewModel.lensContinuationErrors["today"])
    }

    func testNoRetirementReadFastForwardKeepsInFlightContinuation() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(
                    version: 1,
                    lenses: [makeLensSummary(key: "today", segmentCount: 2)]
                ),
                etag: nil
            )
        ]
        let firstSegment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                version: 1,
                segments: [firstSegment],
                sources: [
                    APIBriefingSource(
                        sourceKey: "content:1",
                        kind: "content",
                        id: 1,
                        title: "First",
                        read: false
                    )
                ],
                nextCursor: "today-page-2",
                hasMore: true
            ),
            makeLens(
                key: "today",
                version: 1,
                segments: [makeSegment(id: 9, sourceKeys: ["news:2"])],
                sources: [
                    APIBriefingSource(
                        sourceKey: "news:2",
                        kind: "news",
                        id: 2,
                        title: "Second",
                        read: false
                    )
                ]
            )
        ]
        service.fetchLensDelaysNanoseconds = [0, 700_000_000]
        service.readMarkResponse = APIBriefingReadMarkResponse(
            marked: 1,
            retired: 0,
            version: 2
        )
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [10]
                && service.fetchLensRequests.count == 2
        }
        viewModel.markSegmentSeen(firstSegment)
        await waitForBriefingCondition(timeoutNanoseconds: 1_500_000_000) {
            viewModel.selectedLens?.segments.map(\.id) == [10, 9]
        }

        XCTAssertEqual(service.fetchLensRequests.count, 2)
        XCTAssertEqual(viewModel.index?.version, 2)
        XCTAssertEqual(viewModel.selectedLens?.version, 2)
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 0)
    }

    func testAuthoritativeStructuralReplacementAdvancesDocumentGeneration() async {
        let service = MockBriefingService()
        let summary = makeLensSummary(key: "today")
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [summary]), etag: "v1"),
            .value(makeIndex(version: 2, lenses: [summary]), etag: "v2")
        ]
        service.lensPageResponses["today"] = [
            makeLens(key: "today", version: 1, segments: [makeSegment(id: 10)]),
            makeLens(key: "today", version: 2, segments: [makeSegment(id: 11)])
        ]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens?.segments.map(\.id) == [10] }
        await viewModel.refreshIndex()
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [11]
        }

        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 1)
    }

    func testOrderedLensesAreStoredAndRefreshAfterLocalReadMarks() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        let todaySummary = makeLensSummary(key: "today", title: "Today", position: 2)
        let firstSummary = makeLensSummary(key: "first", title: "First", position: 1)
        service.indexResults = [
            .value(makeIndex(lenses: [todaySummary, firstSummary]), etag: nil)
        ]
        service.lensResponses["first"] = makeLens(key: "first", position: 1, segments: [segment])
        service.lensResponses["today"] = makeLens(key: "today", position: 2)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)

        XCTAssertEqual(viewModel.selectedLensKey, "first")
        XCTAssertEqual(viewModel.orderedLenses.map(\.key), ["first", "today"])
        XCTAssertEqual(viewModel.orderedLenses.first?.unreadSourceCount, 0)
    }

    func testRapidSegmentMarksDoNotCancelInFlightPersistence() async {
        let service = MockBriefingService()
        let summary = makeLensSummary(key: "today", segmentCount: 2)
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [summary]), etag: "v1")
        ]
        service.lensResponses["today"] = makeLens(
            key: "today",
            segments: [
                makeSegment(id: 10, sourceKeys: ["content:1"]),
                makeSegment(id: 11, sourceKeys: ["news:2"]),
            ]
        )
        service.markReadDelaysNanoseconds = [700_000_000, 0]
        service.readMarkResponses = [
            APIBriefingReadMarkResponse(marked: 1, retired: 0, version: 2),
            APIBriefingReadMarkResponse(marked: 1, retired: 0, version: 3),
        ]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        viewModel.markSourcesSeen(["content:1"])
        await waitForBriefingCondition(timeoutNanoseconds: 1_000_000_000) {
            service.markReadCalls.count == 1
        }
        viewModel.markSourcesSeen(["news:2"])

        await waitForBriefingCondition(timeoutNanoseconds: 1_800_000_000) {
            service.markReadCalls.count == 2
                && !viewModel.tasks.isRunning(.readPersistence)
        }
        XCTAssertEqual(
            service.markReadCalls,
            [["content:1"], ["news:2"]]
        )
        XCTAssertEqual(service.markReadCancellationCount, 0)
        XCTAssertEqual(viewModel.index?.version, 3)
    }

    func testConcurrentIndexLoadsShareInFlightRequest() async {
        let service = MockBriefingService()
        service.fetchIndexDelayNanoseconds = 100_000_000
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "briefing-v1")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)

        let first = Task { await viewModel.loadIndexIfNeeded() }
        let second = Task { await viewModel.loadIndexIfNeeded() }
        await first.value
        await second.value
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        XCTAssertEqual(service.indexEtags, [nil])
        XCTAssertEqual(viewModel.state, .loaded)
    }

    func testOneActiveTransitionProducesOneIndexRequest() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: nil)
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)

        viewModel.setActive(true)
        viewModel.setActive(true)
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        XCTAssertEqual(service.indexEtags.count, 1)
    }

    func testInitialIndexFailureIsFatalWithoutSnapshot() async {
        let service = MockBriefingService()
        service.indexError = URLError(.notConnectedToInternet)
        let viewModel = BriefingViewModel(
            service: service,
            initialIndexRetryDelays: [0, 0]
        )

        await viewModel.loadIndexIfNeeded()

        XCTAssertEqual(service.indexEtags.count, 3)
        guard case .error = viewModel.state else {
            return XCTFail("Expected initial index failure to be fatal")
        }
    }

    func testInitialIndexDoesNotRetryDefinitiveFailure() async {
        let service = MockBriefingService()
        service.indexError = APIError.decodingError(
            DecodingError.dataCorrupted(
                .init(codingPath: [], debugDescription: "Invalid fixture")
            )
        )
        let viewModel = BriefingViewModel(
            service: service,
            initialIndexRetryDelays: [0, 0]
        )

        await viewModel.loadIndexIfNeeded()

        XCTAssertEqual(service.indexEtags.count, 1)
        guard case .error = viewModel.state else {
            return XCTFail("Expected definitive initial failure to be fatal")
        }
    }

    func testInitialIndexRetriesTransientTransportFailure() async {
        let service = MockBriefingService()
        service.indexErrors = [URLError(.networkConnectionLost), nil]
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "briefing-v1")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(
            service: service,
            initialIndexRetryDelays: [0]
        )

        await viewModel.loadIndexIfNeeded()

        XCTAssertEqual(service.indexEtags.count, 2)
        XCTAssertEqual(viewModel.state, .loaded)
    }

    func testFreshIndexSkipsLifecycleRevalidationUntilIntervalExpires() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "briefing-v1"),
            .notModified
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        var now = Date(timeIntervalSince1970: 1_800_000_000)
        let viewModel = BriefingViewModel(
            service: service,
            indexFreshnessInterval: 15 * 60,
            now: { now }
        )

        await viewModel.loadIndexIfNeeded()
        now.addTimeInterval(14 * 60)
        await viewModel.loadIndexIfNeeded()

        XCTAssertEqual(service.indexEtags.count, 1)

        now.addTimeInterval(61)
        await viewModel.loadIndexIfNeeded()

        XCTAssertEqual(service.indexEtags, [nil, "briefing-v1"])
    }

    func testFreshRestoredSnapshotSkipsImmediateRevalidationAndLoadsWorkingSet() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore(userID: 1)
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        service.lensResponses["today"] = makeLens(key: "today")
        snapshotStore.snapshotToLoad = BriefingSnapshot(
            userID: 1,
            index: makeIndex(lenses: [makeLensSummary(key: "today")]),
            etag: "etag-1",
            selectedLensKey: "today",
            lenses: [:],
            lastValidatedAt: now.addingTimeInterval(-60),
            savedAt: now.addingTimeInterval(-60)
        )
        let viewModel = BriefingViewModel(
            service: service,
            snapshotStore: snapshotStore,
            indexFreshnessInterval: 15 * 60,
            now: { now }
        )

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        XCTAssertTrue(service.indexEtags.isEmpty)
        XCTAssertEqual(service.fetchLensKeys, ["today"])
        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertEqual(viewModel.selectedLensKey, "today")
    }

    func testRefreshUsesETagAndKeepsExistingIndexOnNotModified() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]), etag: "etag-4"),
            .notModified
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 4)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await viewModel.refreshIndex()

        XCTAssertEqual(service.indexEtags, [nil, "etag-4"])
        XCTAssertEqual(viewModel.index?.version, 4)
        XCTAssertEqual(viewModel.state, .loaded)
    }

    func testMarkSegmentSeenBatchesUnreadSourceKeysAndMarksSourcesReadLocally() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: nil)
        ]
        service.lensResponses["today"] = makeLens(key: "today", segments: [segment])
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 2, retired: 0, version: 2)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
        XCTAssertEqual(viewModel.source(for: "news:2")?.read, true)
        // Read segments stay in the feed (greyed by the view); the server
        // retires them and the next index fetch drops them.
        XCTAssertEqual(viewModel.selectedLens?.segments.count, 1)
        XCTAssertEqual(viewModel.index?.lenses.first?.segmentCount, 1)
        XCTAssertEqual(viewModel.index?.lenses.first?.unreadSourceCount, 0)

        try? await Task.sleep(nanoseconds: 450_000_000)

        XCTAssertEqual(service.markReadCalls, [["content:1", "news:2"]])
        XCTAssertEqual(viewModel.index?.version, 2)
    }

    func testMarkSegmentSeenOptimisticallyMarksReadAndDecrementsOwningLensCount() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        service.indexResults = [
            .value(
                makeIndex(lenses: [makeLensSummary(key: "today"), makeLensSummary(key: "later")]),
                etag: nil
            )
        ]
        service.lensResponses["today"] = makeLens(key: "today", segments: [segment])
        service.lensResponses["later"] = APIBriefingLensResponse(
            version: 1,
            lens: makeLensSummary(key: "later"),
            segments: [makeSegment(id: 99, sourceKeys: ["content:9", "news:8"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "content:9",
                    kind: "content",
                    id: 9,
                    title: "Unrelated",
                    summary: nil,
                    contentType: .article,
                    read: false
                ),
                APIBriefingSource(
                    sourceKey: "news:8",
                    kind: "news",
                    id: 8,
                    title: "Unrelated news",
                    summary: nil,
                    read: false
                )
            ]
        )
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.lenses["today"] != nil && viewModel.lenses["later"] != nil }
        viewModel.markSegmentSeen(segment)

        // All UI-facing state updates before any network flush happens.
        XCTAssertEqual(service.markReadCalls, [])
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
        XCTAssertEqual(viewModel.source(for: "news:2")?.read, true)
        let today = viewModel.index?.lenses.first { $0.key == "today" }
        let later = viewModel.index?.lenses.first { $0.key == "later" }
        XCTAssertEqual(today?.unreadSourceCount, 0)
        XCTAssertEqual(later?.unreadSourceCount, 2)
    }

    func testMarkSegmentSeenRejectsOlderInFlightIndexResponse() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1"])
        let unreadSummary = makeLensSummary(
            key: "today",
            segmentCount: 1,
            unreadSourceCount: 2
        )
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [unreadSummary]), etag: "etag-1"),
            .value(makeIndex(version: 1, lenses: [unreadSummary]), etag: "etag-1")
        ]
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 1,
            segments: [segment]
        )
        service.fetchIndexWaitRequestIndices = [1]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        let refresh = Task { await viewModel.refreshIndex() }
        await waitForBriefingCondition { service.indexEtags.count == 2 }
        viewModel.markSegmentSeen(segment)
        XCTAssertEqual(viewModel.newsUnreadSourceCount, 1)

        service.resumeIndexRequest(at: 1)
        await refresh.value

        XCTAssertEqual(viewModel.newsUnreadSourceCount, 1)
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
    }

    func testReadReconciliationRejectsPreMutationIndexVersion() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1"])
        let unreadSummary = makeLensSummary(
            key: "today",
            segmentCount: 1,
            unreadSourceCount: 2
        )
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [unreadSummary]), etag: "etag-1"),
            .value(makeIndex(version: 1, lenses: [unreadSummary]), etag: "etag-1")
        ]
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 1,
            segments: [segment]
        )
        service.readMarkResponse = APIBriefingReadMarkResponse(
            marked: 1,
            retired: 1,
            version: 2
        )
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)
        await waitForBriefingCondition(timeoutNanoseconds: 1_000_000_000) {
            service.markReadCalls.count == 1
                && service.indexEtags.count == 2
                && !viewModel.tasks.isRunning(.readPersistence)
        }

        XCTAssertEqual(viewModel.index?.version, 2)
        XCTAssertEqual(viewModel.newsUnreadSourceCount, 1)
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
    }

    func testOptimisticUnreadCountFloorsAtZero() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        let staleSummary = APIBriefingLensSummary(
            key: "today",
            tier: .news,
            title: "Today",
            deck: "Latest unread reporting",
            position: 0,
            segmentCount: 1,
            unreadSourceCount: 1
        )
        service.indexResults = [.value(makeIndex(lenses: [staleSummary]), etag: nil)]
        service.lensResponses["today"] = makeLens(key: "today", segments: [segment])
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)

        XCTAssertEqual(viewModel.index?.lenses.first?.unreadSourceCount, 0)
    }

    func testFailedFlushKeepsLocalReadStateAndRequeuesKeys() async {
        let service = MockBriefingService()
        let first = makeSegment(id: 10, sourceKeys: ["content:1"])
        let second = makeSegment(id: 11, sourceKeys: ["news:2"])
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: nil)
        ]
        service.lensResponses["today"] = makeLens(key: "today", segments: [first, second])
        service.markReadError = URLError(.notConnectedToInternet)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(first)
        try? await Task.sleep(nanoseconds: 450_000_000)

        XCTAssertEqual(service.markReadCalls, [["content:1"]])
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)

        // The failed keys ride along with the next flush.
        service.markReadError = nil
        viewModel.markSegmentSeen(second)
        try? await Task.sleep(nanoseconds: 450_000_000)

        XCTAssertEqual(service.markReadCalls, [["content:1"], ["content:1", "news:2"]])
    }

    func testIndexResponseWithoutEtagKeepsPreviousEtag() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 4, lenses: []), etag: "etag-4"),
            .value(makeIndex(version: 5, lenses: []), etag: nil),
            .notModified
        ]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await viewModel.refreshIndex()
        await viewModel.refreshIndex()

        XCTAssertEqual(service.indexEtags, [nil, "etag-4", "etag-4"])
        XCTAssertEqual(viewModel.index?.version, 5)
    }

    func testNewerIndexVersionRefetchesLoadedLens() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 1)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens?.version == 1 }

        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 2,
            segments: [makeSegment(sourceKeys: ["news:2"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "news:2",
                    kind: "news",
                    id: 2,
                    title: "Updated news item",
                    summary: "News summary",
                    read: false,
                    discussion: APIBriefingDiscussion(
                        platform: "hackernews",
                        commentCount: 12,
                        summaryStatus: "completed",
                        overview: "Commenters focused on deployment risk."
                    )
                )
            ]
        )
        await viewModel.refreshIndex()
        await waitForBriefingCondition { viewModel.selectedLens?.version == 2 }

        XCTAssertEqual(service.fetchLensKeys, ["today", "today"])
        XCTAssertEqual(viewModel.source(for: "news:2")?.discussion?.overview, "Commenters focused on deployment risk.")
    }

    func testCancelledOldLensResponseCannotOverwriteNewGeneration() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 1,
            segments: [makeSegment(id: 10)]
        )
        service.fetchLensDelayNanoseconds = 100_000_000
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { service.fetchLensKeys == ["today"] }
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 2,
            segments: [makeSegment(id: 20)]
        )
        await viewModel.refreshIndex()
        await waitForBriefingCondition { viewModel.selectedLens?.version == 2 }

        XCTAssertEqual(viewModel.selectedLens?.segments.first?.id, 20)
        XCTAssertEqual(service.fetchLensKeys, ["today", "today"])
    }

    func testCancelledOldLensErrorCannotOverwriteNewGeneration() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2"),
        ]
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 1,
            segments: [makeSegment(id: 10)]
        )
        service.fetchLensDelaysNanoseconds = [500_000_000, 500_000_000]
        service.fetchLensErrors = [URLError(.networkConnectionLost), nil]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { service.fetchLensKeys == ["today"] }
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 2,
            segments: [makeSegment(id: 20)]
        )

        await viewModel.refreshIndex()
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertNil(viewModel.lensErrors["today"])
        await waitForBriefingCondition(timeoutNanoseconds: 1_000_000_000) {
            viewModel.selectedLens?.version == 2
        }
        XCTAssertEqual(service.fetchLensKeys, ["today", "today"])
    }

    func testSameVersionIndexBodyKeepsLoadedLens() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 8, lenses: [makeLensSummary(key: "today")]), etag: "etag-stale"),
            .value(makeIndex(version: 8, lenses: [makeLensSummary(key: "today")]), etag: "etag-8")
        ]
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 8,
            segments: [makeSegment(id: 10, sourceKeys: ["content:1"])]
        )
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens?.segments.first?.id == 10 }
        let initialRenderModel = viewModel.renderModel(for: "today")

        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 8,
            segments: [makeSegment(id: 11, sourceKeys: ["news:2"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "news:2",
                    kind: "news",
                    id: 2,
                    title: "Fresh news item",
                    summary: "Updated summary",
                    read: false
                )
            ]
        )
        await viewModel.refreshIndex()

        XCTAssertEqual(service.indexEtags, [nil, "etag-stale"])
        XCTAssertEqual(service.fetchLensKeys, ["today"])
        XCTAssertEqual(viewModel.selectedLens?.segments.first?.id, 10)
        XCTAssertTrue(initialRenderModel === viewModel.renderModel(for: "today"))
    }

    func testReadMarkFlushKeepsAffectedLensVisibleAndOmitsItFromSnapshot() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore(userID: 42)
        let todaySegment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.indexResults = [
            .value(
                makeIndex(lenses: [
                    makeLensSummary(key: "today", position: 0),
                    makeLensSummary(key: "later", position: 1)
                ]),
                etag: nil
            )
        ]
        service.lensResponses["today"] = makeLens(key: "today", position: 0, segments: [todaySegment])
        service.lensResponses["later"] = makeLens(
            key: "later",
            position: 1,
            segments: [makeSegment(id: 20, sourceKeys: ["content:9"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "content:9",
                    kind: "content",
                    id: 9,
                    title: "Unrelated",
                    summary: nil,
                    contentType: .article,
                    read: false
                )
            ]
        )
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 1, retired: 0, version: 8)
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition {
            viewModel.lenses["today"] != nil && viewModel.lenses["later"] != nil
        }
        service.indexError = NSError(domain: "BriefingViewModelTests", code: 1)
        viewModel.markSegmentSeen(todaySegment)

        await waitForBriefingCondition { service.markReadCalls.count == 1 }
        await waitForBriefingCondition(timeoutNanoseconds: 1_500_000_000) {
            snapshotStore.savedSnapshots.contains {
                $0.index.version == 8 && $0.lenses["today"] == nil
            }
        }

        let saved = snapshotStore.savedSnapshots.last { $0.index.version == 8 }
        XCTAssertEqual(saved?.userID, 42)
        XCTAssertEqual(viewModel.lenses["today"]?.version, 1)
        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 1)
        XCTAssertEqual(service.indexEtags.count, 2)
        XCTAssertNil(saved?.lenses["today"])
        XCTAssertNotNil(saved?.lenses["later"])
    }

    func testRestoredSnapshotWithMissingLensLoadsItAfterNotModified() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore(userID: 1)
        snapshotStore.snapshotToLoad = BriefingSnapshot(
            userID: 1,
            index: makeIndex(
                version: 4,
                lenses: [
                    makeLensSummary(key: "alpha", position: 0),
                    makeLensSummary(key: "zeta", position: 1)
                ]
            ),
            etag: "etag-4",
            selectedLensKey: "alpha",
            lenses: ["zeta": makeLens(key: "zeta", version: 4, position: 1)],
            savedAt: Date(timeIntervalSince1970: 1_800_000_000)
        )
        service.indexResults = [.notModified]
        service.lensResponses["alpha"] = makeLens(key: "alpha", version: 4, position: 0)
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens?.lens.key == "alpha" }

        XCTAssertEqual(service.indexEtags, ["etag-4"])
        XCTAssertEqual(service.fetchLensKeys, ["alpha"])
        XCTAssertNotNil(viewModel.renderModel(for: "alpha"))
        XCTAssertNil(viewModel.renderModel(for: "zeta"))

        viewModel.selectLens(key: "zeta")

        XCTAssertNotNil(viewModel.renderModel(for: "zeta"))
    }

    func testRestoredPartialSnapshotResumesCursorOnlyAfterNotModified() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore(userID: 1)
        let firstPage = makeLens(
            key: "today",
            version: 4,
            segments: [makeSegment(id: 10, sourceKeys: ["content:1"])],
            nextCursor: "today-page-2",
            hasMore: true
        )
        snapshotStore.snapshotToLoad = BriefingSnapshot(
            userID: 1,
            index: makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]),
            etag: "etag-4",
            selectedLensKey: "today",
            lenses: ["today": firstPage],
            savedAt: Date()
        )
        service.indexResults = [.notModified]
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                version: 4,
                segments: [makeSegment(id: 9, sourceKeys: ["news:2"])]
            )
        ]
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [10, 9]
        }

        XCTAssertEqual(service.indexEtags, ["etag-4"])
        XCTAssertEqual(service.fetchLensRequests.map(\.cursor), ["today-page-2"])
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 0)
    }

    func testNewerIndexRestartsPartialSnapshotAtFirstPage() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore(userID: 1)
        snapshotStore.snapshotToLoad = BriefingSnapshot(
            userID: 1,
            index: makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]),
            etag: "etag-4",
            selectedLensKey: "today",
            lenses: [
                "today": makeLens(
                    key: "today",
                    version: 4,
                    segments: [makeSegment(id: 10)],
                    nextCursor: "stale-page-2",
                    hasMore: true
                )
            ],
            savedAt: Date()
        )
        service.indexResults = [
            .value(
                makeIndex(version: 5, lenses: [makeLensSummary(key: "today")]),
                etag: "etag-5"
            )
        ]
        service.lensPageResponses["today"] = [
            makeLens(
                key: "today",
                version: 5,
                segments: [makeSegment(id: 20, sourceKeys: ["news:2"])]
            )
        ]
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition {
            viewModel.selectedLens?.segments.map(\.id) == [20]
        }

        XCTAssertEqual(service.fetchLensRequests.map(\.cursor), [nil])
        XCTAssertEqual(viewModel.selectedLens?.version, 5)
        XCTAssertEqual(viewModel.documentGeneration(for: "today"), 1)
    }

    func testOfflineRevalidationKeepsRestoredSnapshotReady() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore(userID: 1)
        snapshotStore.snapshotToLoad = BriefingSnapshot(
            userID: 1,
            index: makeIndex(lenses: [makeLensSummary(key: "today")]),
            etag: "etag-1",
            selectedLensKey: "today",
            lenses: ["today": makeLens(key: "today")],
            savedAt: Date()
        )
        service.indexError = URLError(.notConnectedToInternet)
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertNotNil(viewModel.selectedLens)
    }

    func testCitationLinkedMarkdownWrapsBracketNumbers() {
        let summary = "Fact one [1]. **Bold** fact [12]."

        let linked = BriefingDigViewModel.citationLinkedMarkdown(summary)

        XCTAssertEqual(
            linked,
            "Fact one [\\[1\\]](digsource://1). **Bold** fact [\\[12\\]](digsource://12)."
        )
    }

}
