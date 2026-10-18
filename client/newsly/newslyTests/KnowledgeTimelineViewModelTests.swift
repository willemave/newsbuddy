import XCTest
@testable import newsly

@MainActor
final class KnowledgeTimelineViewModelTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_780_771_260)

    func testLoadsIndependentSourcesAndKeepsPartialSuccess() async {
        let saved = makeSaved(id: 11, activityDate: now)
        let store = makeStore(
            saved: [saved],
            chatLoadFails: true,
            decks: [makeDeck(id: 12, activityDate: now.addingTimeInterval(-60))],
            narrations: [makeNarration(id: 13, activityDate: now.addingTimeInterval(-120))]
        )

        await store.forceReload()

        XCTAssertEqual(store.timeline.map(\.id), ["saved-11", "deck-12", "narration-13"])
        XCTAssertEqual(store.failures.map(\.id), ["chats.load"])

        await store.savedContent.toggleKnowledgeSave(saved.id)

        XCTAssertEqual(store.failures.map(\.id), ["saved.action", "chats.load"])

        await store.forceReload()

        XCTAssertEqual(store.failures.map(\.id), ["saved.action", "chats.load"])
    }

    func testProjectionMaterializesWhenASourceChangesAndRepeatedReadsStayStable() async {
        let store = makeStore()
        let session = makeSession(id: 21, activityDate: now)

        store.chats.sessions = [session]
        await Task.yield()

        let expectedIDs = ["chat-21"]
        XCTAssertEqual(store.timeline.map(\.id), expectedIDs)
        XCTAssertEqual(store.groupedTimeline.flatMap(\.items).map(\.id), expectedIDs)
        for _ in 0..<5 {
            XCTAssertEqual(store.timeline.map(\.id), expectedIDs)
            XCTAssertEqual(store.groupedTimeline.flatMap(\.items).map(\.id), expectedIDs)
        }
    }

    func testInitialLoadPublishesOneMergedSnapshotAfterAllSourcesFinish() async {
        let saved = makeSaved(id: 31, activityDate: now)
        let narration = makeNarration(id: 32, activityDate: now.addingTimeInterval(-60))
        let store = makeStore(
            saved: [saved],
            narrations: [narration],
            savedLoadDelayNanoseconds: 500_000_000
        )

        XCTAssertTrue(store.isLoading)
        XCTAssertTrue(store.timeline.isEmpty)

        let load = Task { await store.forceReload() }
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(store.narrations.episodes.map(\.id), [narration.id])
        XCTAssertTrue(store.savedContent.contents.isEmpty)
        XCTAssertTrue(store.timeline.isEmpty)
        XCTAssertTrue(store.isLoading)

        await load.value

        XCTAssertEqual(store.timeline.map(\.id), ["saved-31", "narration-32"])
        XCTAssertFalse(store.isLoading)
    }

    func testCancelledExplicitInitialReloadContinuesToOneAggregatePublication() async {
        let saved = makeSaved(id: 41, activityDate: now)
        let store = makeStore(
            saved: [saved],
            savedLoadDelayNanoseconds: 500_000_000
        )

        let cancelledLoad = Task { await store.forceReload() }
        try? await Task.sleep(nanoseconds: 100_000_000)
        cancelledLoad.cancel()
        await cancelledLoad.value

        XCTAssertEqual(store.timeline.map(\.id), ["saved-41"])
        XCTAssertFalse(store.isLoading)
    }

    func testCancelledLifecycleWaiterCannotCancelJoinedExplicitReload() async throws {
        let contentService = ControlledKnowledgeTimelineContentService()
        let store = makeStore(contentService: contentService, now: { self.now })
        let lifecycle = AppLifecycle(now: { self.now })
        lifecycle.record(.active)
        let activation = try XCTUnwrap(lifecycle.activation)

        let lifecycleRead = Task { await store.activate(activation) }
        await contentService.waitForPendingRequestCount(1)
        let explicitRead = Task { await store.forceReload() }
        // `forceReload` has no synchronous work before it promotes the shared
        // aggregate. Let that main-actor turn reach its first suspension.
        await Task.yield()

        lifecycleRead.cancel()
        store.suspendAutomaticReads()
        contentService.resolveRequest(
            at: 0,
            with: [makeSaved(id: 42, activityDate: now)]
        )

        await explicitRead.value
        await lifecycleRead.value

        XCTAssertEqual(store.timeline.map(\.id), ["saved-42"])
        XCTAssertFalse(store.isLoading)
    }

    func testInterruptionReturnDoesNotRevalidateSameActivation() async throws {
        var currentDate = now
        let lifecycle = AppLifecycle(now: { currentDate })
        let contentService = KnowledgeTimelineContentService(contents: [])
        let store = makeStore(contentService: contentService, now: { currentDate })

        lifecycle.record(.active)
        await store.activate(try XCTUnwrap(lifecycle.activation))
        XCTAssertTrue(store.automaticReadsEnabled)

        currentDate = currentDate.addingTimeInterval(2)
        lifecycle.record(.inactive)
        XCTAssertTrue(store.automaticReadsEnabled)
        currentDate = currentDate.addingTimeInterval(2)
        lifecycle.record(.active)
        await store.activate(try XCTUnwrap(lifecycle.activation))

        XCTAssertEqual(contentService.requestCount, 1)
        XCTAssertEqual(store.lastHandledActivationGeneration, 1)
        XCTAssertTrue(store.automaticReadsEnabled)
    }

    func testRouteReturnInSameActivationResumesWithoutRevalidation() async throws {
        let contentService = KnowledgeTimelineContentService(contents: [])
        let store = makeStore(contentService: contentService, now: { self.now })
        let activation = AppLifecycle.Activation(
            generation: 1,
            kind: .initialLaunch,
            occurredAt: now,
            backgroundDuration: nil
        )

        await store.activate(activation)
        store.suspendAutomaticReads()
        await store.activate(activation)

        XCTAssertEqual(contentService.requestCount, 1)
        XCTAssertTrue(store.automaticReadsEnabled)
    }

    func testStaleWarmResumeCoalescesAndKeepsRowsDuringRevalidation() async throws {
        var currentDate = now
        let initial = makeSaved(id: 51, activityDate: now)
        let replacement = makeSaved(id: 52, activityDate: now.addingTimeInterval(60))
        let contentService = KnowledgeTimelineContentService(
            responses: [[initial], [replacement]],
            loadDelaysNanoseconds: [0, 500_000_000]
        )
        let store = makeStore(
            contentService: contentService,
            freshnessPolicy: KnowledgeFreshnessPolicy(revalidationInterval: 60),
            now: { currentDate }
        )
        let lifecycle = AppLifecycle(now: { currentDate })

        lifecycle.record(.active)
        await store.activate(try XCTUnwrap(lifecycle.activation))
        XCTAssertEqual(store.timeline.map(\.id), ["saved-51"])

        currentDate = currentDate.addingTimeInterval(120)
        lifecycle.record(.background)
        store.suspendAutomaticReads()
        lifecycle.record(.active)
        let activation = try XCTUnwrap(lifecycle.activation)

        let first = Task { await store.activate(activation) }
        let second = Task { await store.activate(activation) }
        try await Task.sleep(for: .milliseconds(100))

        XCTAssertEqual(store.timeline.map(\.id), ["saved-51"])
        XCTAssertTrue(store.isLoading)
        XCTAssertEqual(contentService.requestCount, 2)

        await first.value
        await second.value

        XCTAssertEqual(store.timeline.map(\.id), ["saved-52"])
        XCTAssertEqual(contentService.requestCount, 2)
        XCTAssertEqual(store.lastHandledActivationGeneration, 2)
    }

    func testFreshWarmResumeSkipsRevalidation() async throws {
        var currentDate = now
        let contentService = KnowledgeTimelineContentService(contents: [])
        let store = makeStore(
            contentService: contentService,
            freshnessPolicy: KnowledgeFreshnessPolicy(revalidationInterval: 60),
            now: { currentDate }
        )
        let lifecycle = AppLifecycle(now: { currentDate })

        lifecycle.record(.active)
        await store.activate(try XCTUnwrap(lifecycle.activation))
        currentDate = currentDate.addingTimeInterval(30)
        lifecycle.record(.background)
        store.suspendAutomaticReads()
        lifecycle.record(.active)

        await store.activate(try XCTUnwrap(lifecycle.activation))

        XCTAssertEqual(contentService.requestCount, 1)
        XCTAssertEqual(store.lastHandledActivationGeneration, 2)
    }

    func testRevalidationFailureKeepsPublishedRowsAndSourceError() async throws {
        var currentDate = now
        let initial = makeSaved(id: 71, activityDate: now)
        let contentService = KnowledgeTimelineContentService(
            responses: [[initial], []],
            loadDelaysNanoseconds: [0, 0],
            failingRequestIndices: [1]
        )
        let store = makeStore(
            contentService: contentService,
            freshnessPolicy: KnowledgeFreshnessPolicy(revalidationInterval: 0),
            now: { currentDate }
        )
        let lifecycle = AppLifecycle(now: { currentDate })

        lifecycle.record(.active)
        await store.activate(try XCTUnwrap(lifecycle.activation))
        currentDate = currentDate.addingTimeInterval(120)
        lifecycle.record(.background)
        store.suspendAutomaticReads()
        lifecycle.record(.active)

        await store.activate(try XCTUnwrap(lifecycle.activation))

        XCTAssertEqual(store.timeline.map(\.id), ["saved-71"])
        XCTAssertEqual(store.failures.map(\.id), ["saved.load"])
        XCTAssertEqual(contentService.requestCount, 2)
    }

    func testBackgroundCancellationFencesLateInitialResult() async throws {
        var currentDate = now
        let contentService = ControlledKnowledgeTimelineContentService()
        let store = makeStore(
            contentService: contentService,
            freshnessPolicy: KnowledgeFreshnessPolicy(revalidationInterval: 0),
            now: { currentDate }
        )
        let lifecycle = AppLifecycle(now: { currentDate })

        lifecycle.record(.active)
        let initialActivation = try XCTUnwrap(lifecycle.activation)
        let initial = Task { await store.activate(initialActivation) }
        await contentService.waitForPendingRequestCount(1)

        lifecycle.record(.background)
        store.suspendAutomaticReads()
        XCTAssertFalse(store.automaticReadsEnabled)
        currentDate = currentDate.addingTimeInterval(120)
        lifecycle.record(.active)
        let warmActivation = try XCTUnwrap(lifecycle.activation)
        let warm = Task { await store.activate(warmActivation) }
        await contentService.waitForPendingRequestCount(2)

        contentService.resolveRequest(
            at: 1,
            with: [makeSaved(id: 62, activityDate: now)]
        )
        await warm.value
        XCTAssertEqual(store.timeline.map(\.id), ["saved-62"])

        contentService.resolveRequest(
            at: 0,
            with: [makeSaved(id: 61, activityDate: now.addingTimeInterval(-60))]
        )
        await initial.value

        XCTAssertEqual(store.timeline.map(\.id), ["saved-62"])
        XCTAssertEqual(store.lastHandledActivationGeneration, 2)
    }

    func testBackgroundDuringRevalidationKeepsRowsUntilNextWarmResume() async throws {
        var currentDate = now
        let contentService = ControlledKnowledgeTimelineContentService()
        let initialNarration = makeNarration(id: 91, activityDate: now.addingTimeInterval(-60))
        let interruptedNarration = makeNarration(id: 92, activityDate: now.addingTimeInterval(60))
        let replacementNarration = makeNarration(id: 93, activityDate: now.addingTimeInterval(180))
        let narrationService = KnowledgeTimelineNarrationService(
            responses: [[initialNarration], [interruptedNarration], [replacementNarration]]
        )
        let store = makeStore(
            contentService: contentService,
            narrationService: narrationService,
            freshnessPolicy: KnowledgeFreshnessPolicy(revalidationInterval: 0),
            now: { currentDate }
        )
        let lifecycle = AppLifecycle(now: { currentDate })

        lifecycle.record(.active)
        let initialActivation = try XCTUnwrap(lifecycle.activation)
        let initial = Task { await store.activate(initialActivation) }
        await contentService.waitForPendingRequestCount(1)
        contentService.resolveRequest(
            at: 0,
            with: [makeSaved(id: 81, activityDate: now)]
        )
        await initial.value
        XCTAssertEqual(store.timeline.map(\.id), ["saved-81", "narration-91"])

        currentDate = currentDate.addingTimeInterval(120)
        lifecycle.record(.background)
        store.suspendAutomaticReads()
        lifecycle.record(.active)
        let staleActivation = try XCTUnwrap(lifecycle.activation)
        let stale = Task { await store.activate(staleActivation) }
        await contentService.waitForPendingRequestCount(1)

        lifecycle.record(.background)
        store.suspendAutomaticReads()
        contentService.resolveRequest(
            at: 0,
            with: [makeSaved(id: 82, activityDate: now.addingTimeInterval(60))]
        )
        await stale.value

        XCTAssertEqual(store.timeline.map(\.id), ["saved-81", "narration-91"])

        currentDate = currentDate.addingTimeInterval(120)
        lifecycle.record(.active)
        let replacementActivation = try XCTUnwrap(lifecycle.activation)
        let replacement = Task { await store.activate(replacementActivation) }
        await contentService.waitForPendingRequestCount(1)
        contentService.resolveRequest(
            at: 0,
            with: [makeSaved(id: 83, activityDate: now.addingTimeInterval(120))]
        )
        await replacement.value

        XCTAssertEqual(store.timeline.map(\.id), ["narration-93", "saved-83"])
        XCTAssertEqual(store.lastHandledActivationGeneration, 3)
    }

    func testCancelledRevalidationReleasesSourceObservationWithoutPublishingLateRead() async throws {
        var currentDate = now
        let contentService = ControlledKnowledgeTimelineContentService()
        let store = makeStore(
            contentService: contentService,
            freshnessPolicy: KnowledgeFreshnessPolicy(revalidationInterval: 0),
            now: { currentDate }
        )
        let lifecycle = AppLifecycle(now: { currentDate })

        lifecycle.record(.active)
        let initialActivation = try XCTUnwrap(lifecycle.activation)
        let initial = Task { await store.activate(initialActivation) }
        await contentService.waitForPendingRequestCount(1)
        contentService.resolveRequest(
            at: 0,
            with: [makeSaved(id: 101, activityDate: now)]
        )
        await initial.value

        currentDate = currentDate.addingTimeInterval(120)
        lifecycle.record(.background)
        store.suspendAutomaticReads()
        lifecycle.record(.active)
        let revalidationActivation = try XCTUnwrap(lifecycle.activation)
        let revalidation = Task { await store.activate(revalidationActivation) }
        await contentService.waitForPendingRequestCount(1)

        lifecycle.record(.background)
        store.suspendAutomaticReads()
        store.chats.sessions = [
            makeSession(id: 102, activityDate: now.addingTimeInterval(60))
        ]
        await Task.yield()

        XCTAssertEqual(store.timeline.map(\.id), ["chat-102", "saved-101"])

        contentService.resolveRequest(
            at: 0,
            with: [makeSaved(id: 103, activityDate: now.addingTimeInterval(120))]
        )
        await revalidation.value

        XCTAssertEqual(store.timeline.map(\.id), ["chat-102", "saved-101"])
    }

    func testNewSaveRevalidatesRecentSameActivationAndDoesNotConsumeConcurrentSave() async {
        let service = ControlledKnowledgeTimelineContentService()
        var signal: String?
        let store = makeStore(contentService: service, saveSignalToken: { signal })
        let activation = AppLifecycle.Activation(generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil)
        let initial = Task { await store.activate(activation) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [])
        await initial.value

        signal = "first"
        let firstSave = Task { await store.activate(activation) }
        await service.waitForPendingRequestCount(1)
        signal = "second"
        service.resolveRequest(at: 0, with: [makeSaved(id: 51, activityDate: now)])
        await firstSave.value

        let secondSave = Task { await store.activate(activation) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 52, activityDate: now)])
        await secondSave.value
        XCTAssertEqual(store.timeline.map(\.id), ["saved-52"])
        await store.activate(activation)
        await service.waitForPendingRequestCount(0)
    }

    func testFailedSaveReadKeepsSignalPendingForSameActivation() async {
        let service = ControlledKnowledgeTimelineContentService()
        var signal: String?
        let store = makeStore(contentService: service, saveSignalToken: { signal })
        let activation = AppLifecycle.Activation(
            generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil
        )
        let initial = Task { await store.activate(activation) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [])
        await initial.value
        signal = "saved"
        service.failNextRequest = true
        await store.activate(activation)
        XCTAssertNotNil(store.savedContent.initialLoadErrorMessage)
        let retry = Task { await store.activate(activation) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 53, activityDate: now)])
        await retry.value
        XCTAssertEqual(store.timeline.map(\.id), ["saved-53"])
    }

    func testVisibleSavedRevalidationUpdatesRowsAndStopsWhenSuspended() async {
        let service = ControlledKnowledgeTimelineContentService()
        let store = makeStore(contentService: service)
        let initial = Task { await store.activate(.init(generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil)) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 61, activityDate: now, status: .processing)])
        await initial.value
        XCTAssertEqual(store.savedContent.contents.first?.savedLibraryItemState, .processing)

        for status in [APIContentStatus.completed, .failed] {
            let refresh = Task { await store.revalidateSavedKnowledgeIfIdle() }
            await service.waitForPendingRequestCount(1)
            XCTAssertEqual(store.timeline.map(\.id), ["saved-61"])
            service.resolveRequest(at: 0, with: [makeSaved(id: 61, activityDate: now, status: status)])
            await refresh.value
            XCTAssertEqual(store.savedContent.contents.first?.status, status)
            if case .saved(let entry) = store.timeline.first {
                XCTAssertEqual(entry.content.status, status)
            } else { XCTFail("Saved row disappeared") }
        }
        store.suspendAutomaticReads()
        await store.revalidateSavedKnowledgeIfIdle()
        await service.waitForPendingRequestCount(0)
    }

    func testSuspendCancelsStandaloneSavedObservationAndFencesLateResponse() async {
        let service = ControlledKnowledgeTimelineContentService()
        let store = makeStore(contentService: service)
        let initial = Task { await store.activate(.init(
            generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil
        )) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 71, activityDate: now)])
        await initial.value

        let poll = Task { await store.revalidateSavedKnowledgeIfIdle() }
        await service.waitForPendingRequestCount(1)
        XCTAssertTrue(store.savedContent.isReadInFlight)
        store.suspendAutomaticReads()
        XCTAssertFalse(store.savedContent.isReadInFlight)
        service.resolveRequest(at: 0, with: [makeSaved(id: 72, activityDate: now)])
        await poll.value
        XCTAssertEqual(store.timeline.map(\.id), ["saved-71"])
        XCTAssertNil(store.savedContent.initialLoadErrorMessage)
    }

    func testSuspendCannotCancelExplicitAggregateThatPreemptedSavedObservation() async {
        let service = ControlledKnowledgeTimelineContentService()
        let store = makeStore(contentService: service)
        let initial = Task { await store.activate(.init(
            generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil
        )) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 81, activityDate: now)])
        await initial.value

        let poll = Task { await store.revalidateSavedKnowledgeIfIdle() }
        await service.waitForPendingRequestCount(1)
        let explicit = Task { await store.forceReload() }
        await service.waitForPendingRequestCount(2)
        store.suspendAutomaticReads()
        XCTAssertTrue(store.savedContent.isReadInFlight)
        service.resolveRequest(at: 1, with: [makeSaved(id: 82, activityDate: now)])
        await explicit.value
        service.resolveRequest(at: 0, with: [makeSaved(id: 83, activityDate: now)])
        await poll.value
        XCTAssertEqual(store.timeline.map(\.id), ["saved-82"])
        XCTAssertNil(store.savedContent.initialLoadErrorMessage)
    }

    func testFastObservationExpiresAndOnlyNewSaveOrActivationRestartsIt() async {
        var date = Date()
        var signal: String?
        let store = makeStore(
            saved: [makeSaved(id: 91, activityDate: date, status: .processing, createdAt: date)],
            saveSignalToken: { signal }, now: { date }
        )
        let activation = AppLifecycle.Activation(
            generation: 1, kind: .initialLaunch, occurredAt: date, backgroundDuration: nil
        )
        await store.activate(activation)
        XCTAssertEqual(store.savedObservationInterval, .seconds(5))
        date = date.addingTimeInterval(121)
        XCTAssertEqual(store.savedObservationInterval, .seconds(30))
        await store.activate(activation)
        XCTAssertEqual(store.savedObservationInterval, .seconds(30))
        signal = "new-save"
        await store.activate(activation)
        XCTAssertEqual(store.savedObservationInterval, .seconds(5))
        date = date.addingTimeInterval(121)
        await store.revalidateSavedKnowledgeIfIdle()
        XCTAssertEqual(store.savedObservationInterval, .seconds(30))
        await store.activate(.init(
            generation: 2, kind: .warmResume, occurredAt: date, backgroundDuration: .seconds(30)
        ))
        XCTAssertEqual(store.savedObservationInterval, .seconds(5))
    }

    func testObservationRetainsLoadErrorsUntilSuccessAndNeverClearsPaginationError() async {
        let service = ControlledKnowledgeTimelineContentService()
        service.nextCursor = "next"
        let store = makeStore(contentService: service)
        let initial = Task { await store.activate(.init(
            generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil
        )) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 101, activityDate: now)])
        await initial.value
        service.failNextRequest = true
        await store.savedContent.loadMoreContent()
        let paginationError = store.savedContent.loadMoreErrorMessage
        XCTAssertNotNil(paginationError)
        service.failNextRequest = true
        await store.revalidateSavedKnowledgeIfIdle()
        let loadError = store.savedContent.initialLoadErrorMessage
        XCTAssertNotNil(loadError)

        let poll = Task { await store.revalidateSavedKnowledgeIfIdle() }
        await service.waitForPendingRequestCount(1)
        XCTAssertEqual(store.savedContent.initialLoadErrorMessage, loadError)
        XCTAssertEqual(store.savedContent.loadMoreErrorMessage, paginationError)
        service.resolveRequest(at: 0, with: [makeSaved(id: 101, activityDate: now)])
        await poll.value
        XCTAssertNil(store.savedContent.initialLoadErrorMessage)
        XCTAssertEqual(store.savedContent.loadMoreErrorMessage, paginationError)
        XCTAssertTrue(store.hasPaginationError)

        let supersededPoll = Task { await store.revalidateSavedKnowledgeIfIdle() }
        await service.waitForPendingRequestCount(1)
        service.failNextRequest = true
        await store.loadNextPage()
        XCTAssertNil(store.savedContent.initialLoadErrorMessage)
        XCTAssertNotNil(store.savedContent.loadMoreErrorMessage)
        service.resolveRequest(at: 0, with: [makeSaved(id: 102, activityDate: now)])
        await supersededPoll.value
        XCTAssertNil(store.savedContent.initialLoadErrorMessage)
        XCTAssertNotNil(store.savedContent.loadMoreErrorMessage)
    }

    func testUserPaginationPreemptsObservationAndLatePollCannotReplaceAppendedRows() async {
        let service = ControlledKnowledgeTimelineContentService()
        service.nextCursor = "next"
        var signal: String?
        let store = makeStore(contentService: service, saveSignalToken: { signal })
        let initial = Task { await store.activate(.init(
            generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil
        )) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 111, activityDate: now)])
        await initial.value

        signal = "new-save"
        let poll = Task { await store.revalidateSavedKnowledgeIfIdle() }
        await service.waitForPendingRequestCount(1)
        let pagination = Task { await store.loadNextPage() }
        await service.waitForPendingRequestCount(2)
        service.nextCursor = nil
        service.resolveRequest(at: 1, with: [makeSaved(id: 112, activityDate: now.addingTimeInterval(-1))])
        await pagination.value
        service.resolveRequest(at: 0, with: [makeSaved(id: 113, activityDate: now)])
        await poll.value
        XCTAssertEqual(store.savedContent.contents.map(\.id), [111, 112])
        XCTAssertEqual(store.timeline.map(\.id), ["saved-111", "saved-112"])
        let signalRetry = Task { await store.activate(.init(
            generation: 1, kind: .initialLaunch, occurredAt: now, backgroundDuration: nil
        )) }
        await service.waitForPendingRequestCount(1)
        service.resolveRequest(at: 0, with: [makeSaved(id: 114, activityDate: now)])
        await signalRetry.value
        XCTAssertEqual(store.timeline.map(\.id), ["saved-114"])
    }

    func testSaveSignalsArePartitionedByAccountAndServer() throws {
        let suite = "KnowledgeSaveSignalTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let first = KnowledgeSaveSignalStore(defaults: defaults, server: "https://one", userID: 1)
        let otherUser = KnowledgeSaveSignalStore(defaults: defaults, server: "https://one", userID: 2)
        let otherServer = KnowledgeSaveSignalStore(defaults: defaults, server: "https://two", userID: 1)
        XCTAssertNil(first.token)
        first.recordSave()
        let token = try XCTUnwrap(first.token)
        XCTAssertNil(otherUser.token)
        XCTAssertNil(otherServer.token)
        first.recordSave()
        XCTAssertNotEqual(first.token, token)
    }

    private func makeStore(
        saved: [ContentSummary] = [],
        chatLoadFails: Bool = false,
        decks: [LearningDeck] = [],
        narrations: [AudioEpisode] = [],
        savedLoadDelayNanoseconds: UInt64 = 0,
        contentService: (any ContentSummaryListServicing)? = nil,
        narrationService: (any CustomNarrationLibraryServicing)? = nil,
        freshnessPolicy: KnowledgeFreshnessPolicy = .standard,
        saveSignalToken: @escaping () -> String? = { nil },
        now: @escaping () -> Date = Date.init
    ) -> KnowledgeTimelineViewModel {
        KnowledgeTimelineViewModel(
            savedContent: ContentListViewModel(
                contentService: contentService ?? KnowledgeTimelineContentService(
                    contents: saved,
                    loadDelayNanoseconds: savedLoadDelayNanoseconds
                ),
                readStateCache: makeTestReadStateCache()
            ),
            chats: KnowledgeChatViewModel(
                chatService: KnowledgeTimelineChatService(loadFails: chatLoadFails),
                transcriptionService: TestUnavailableSpeechTranscriber()
            ),
            decks: LearningDecksViewModel(
                service: KnowledgeTimelineDeckService(decks: decks)
            ),
            narrations: CustomNarrationLibraryViewModel(
                playbackService: NarrationPlaybackService(),
                audioService: narrationService
                    ?? KnowledgeTimelineNarrationService(episodes: narrations),
                badgeStatsStore: makeTestBadgeStatsStore(),
                readStateCache: makeTestReadStateCache()
            ),
            freshnessPolicy: freshnessPolicy,
            saveSignalToken: saveSignalToken,
            now: now
        )
    }

    private func makeSession(id: Int, activityDate: Date) -> ChatSessionSummary {
        .fixture(
            id: id,
            title: "Chat \(id)",
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate,
            lastMessageAt: activityDate,
            lastMessagePreview: "A materialized preview"
        )
    }

    private func makeSaved(
        id: Int, activityDate: Date, status: APIContentStatus = .completed, createdAt: Date? = nil
    ) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: .article,
            url: "https://example.com/\(id)",
            title: "Saved \(id)",
            source: "Example",
            platform: nil,
            status: status,
            shortSummary: "Summary",
            createdAt: ServerDate.format(createdAt ?? activityDate.addingTimeInterval(-365 * 24 * 60 * 60)),
            processedAt: nil,
            isRead: false,
            isSavedToKnowledge: true,
            knowledgeSavedAt: ServerDate.format(activityDate)
        )
    }

    private func makeDeck(id: Int, activityDate: Date) -> LearningDeck {
        LearningDeck(
            id: id,
            title: "Deck \(id)",
            sourceKind: .content,
            sourceURL: nil,
            sourceContentId: nil,
            sourceTitle: nil,
            sourceMetadata: [:],
            status: .completed,
            shareEnabled: false,
            viewerAvailable: true,
            latestSuccessfulRunId: nil,
            latestRun: nil,
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate
        )
    }

    private func makeNarration(id: Int, activityDate: Date) -> AudioEpisode {
        AudioEpisode(
            id: id,
            kind: .custom_narration,
            status: .completed,
            title: "Narration \(id)",
            sourceContentId: nil,
            subtitle: nil,
            artworkUrl: nil,
            durationSeconds: nil,
            audioUrl: nil,
            streamUrl: nil,
            scriptText: nil,
            errorMessage: nil,
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate
        )
    }
}

@MainActor
private final class KnowledgeTimelineChatService: KnowledgeChatServicing {
    private enum Failure: Error { case unavailable }
    private let loadFails: Bool

    init(loadFails: Bool) {
        self.loadFails = loadFails
    }

    func listSessionsPage(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int,
        cursor: String?
    ) async throws -> ChatSessionListResponse {
        if loadFails { throw Failure.unavailable }
        return ChatSessionListResponse(
            sessions: [],
            meta: PaginationMetadata(
                nextCursor: nil,
                hasMore: false,
                pageSize: 0,
                total: nil
            )
        )
    }

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        throw Failure.unavailable
    }

    func deleteSession(sessionId: Int) async throws {
        throw Failure.unavailable
    }

    func existingSourceChat(for source: KnowledgeSource) async throws -> ChatSessionSummary? {
        throw Failure.unavailable
    }

    func startSourceChat(for source: KnowledgeSource) async throws -> ChatSessionSummary {
        throw Failure.unavailable
    }
}

@MainActor
private final class KnowledgeTimelineContentService: ContentSummaryListServicing {
    private enum Failure: Error { case unavailable, unused }
    private let responses: [[ContentSummary]]
    private let loadDelaysNanoseconds: [UInt64]
    private let failingRequestIndices: Set<Int>
    private(set) var requestCount = 0

    init(contents: [ContentSummary], loadDelayNanoseconds: UInt64) {
        self.responses = [contents]
        self.loadDelaysNanoseconds = [loadDelayNanoseconds]
        self.failingRequestIndices = []
    }

    convenience init(contents: [ContentSummary]) {
        self.init(contents: contents, loadDelayNanoseconds: 0)
    }

    init(
        responses: [[ContentSummary]],
        loadDelaysNanoseconds: [UInt64],
        failingRequestIndices: Set<Int> = []
    ) {
        self.responses = responses
        self.loadDelaysNanoseconds = loadDelaysNanoseconds
        self.failingRequestIndices = failingRequestIndices
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        let requestIndex = requestCount
        requestCount += 1
        let delay = loadDelaysNanoseconds.indices.contains(requestIndex)
            ? loadDelaysNanoseconds[requestIndex]
            : 0
        try await Task.sleep(nanoseconds: delay)
        if failingRequestIndices.contains(requestIndex) {
            throw Failure.unavailable
        }
        let contents = responses[min(requestIndex, responses.count - 1)]
        return ContentListResponse(
            contents: contents,
            availableDates: [],
            contentTypes: [],
            meta: PaginationMetadata(
                nextCursor: nil,
                hasMore: false,
                pageSize: contents.count,
                total: nil
            )
        )
    }

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse { throw Failure.unused }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw Failure.unused
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw Failure.unused
    }

    func reprocessKnowledgeItem(id: Int) async throws -> KnowledgeMutationResponse {
        throw Failure.unused
    }

    func downloadMoreFromSeries(
        contentId: Int,
        count: Int
    ) async throws -> DownloadMoreResponse { throw Failure.unused }

    func markContentAsUnread(id: Int) async throws { throw Failure.unused }
}

@MainActor
private final class ControlledKnowledgeTimelineContentService: ContentSummaryListServicing {
    private enum Failure: Error { case unused }
    private struct PendingRequest {
        let continuation: CheckedContinuation<[ContentSummary], Never>
    }

    private var pendingRequests: [PendingRequest] = []
    var failNextRequest = false
    var nextCursor: String?

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        if failNextRequest {
            failNextRequest = false
            throw Failure.unused
        }
        let contents = await withCheckedContinuation { continuation in
            pendingRequests.append(PendingRequest(continuation: continuation))
        }
        return ContentListResponse(
            contents: contents,
            availableDates: [],
            contentTypes: [],
            meta: PaginationMetadata(
                nextCursor: nextCursor,
                hasMore: nextCursor != nil,
                pageSize: contents.count,
                total: nil
            )
        )
    }

    func resolveRequest(at index: Int, with contents: [ContentSummary]) {
        pendingRequests.remove(at: index).continuation.resume(returning: contents)
    }

    func waitForPendingRequestCount(
        _ expectedCount: Int,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if pendingRequests.count == expectedCount { return }
            try? await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertEqual(pendingRequests.count, expectedCount, file: file, line: line)
    }

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse { throw Failure.unused }
    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse { throw Failure.unused }
    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse { throw Failure.unused }
    func reprocessKnowledgeItem(id: Int) async throws -> KnowledgeMutationResponse { throw Failure.unused }
    func downloadMoreFromSeries(
        contentId: Int,
        count: Int
    ) async throws -> DownloadMoreResponse { throw Failure.unused }
    func markContentAsUnread(id: Int) async throws { throw Failure.unused }
}

@MainActor
private final class KnowledgeTimelineDeckService: LearningDeckServicing {
    private enum Failure: Error { case unused }
    let decks: [LearningDeck]

    init(decks: [LearningDeck]) {
        self.decks = decks
    }

    func listDecks() async throws -> LearningDeckListResponse {
        LearningDeckListResponse(decks: decks)
    }

    func fetchDeck(id: Int) async throws -> LearningDeck { throw Failure.unused }

    func createDeck(
        contentId: Int?,
        newsItemId: Int?,
        url: String?,
        interestsPrompt: String?
    ) async throws -> LearningDeck { throw Failure.unused }

    func retryDeck(deckId: Int) async throws -> LearningDeck { throw Failure.unused }
    func viewerURL(deckId: Int) async throws -> URL { throw Failure.unused }
    func sourceNotesURL(deckId: Int) async throws -> URL { throw Failure.unused }
    func deleteDeck(deckId: Int) async throws { throw Failure.unused }
}

@MainActor
private final class KnowledgeTimelineNarrationService: CustomNarrationLibraryServicing {
    private enum Failure: Error { case unused }
    private var responses: [[AudioEpisode]]

    init(episodes: [AudioEpisode]) {
        self.responses = [episodes]
    }

    init(responses: [[AudioEpisode]]) {
        self.responses = responses
    }

    func createCustomNarrationEpisode(
        contentIds: [Int],
        newsItemIds: [Int],
        title: String?,
        markSourceContentReadOnPlay: Bool
    ) async throws -> AudioEpisode { throw Failure.unused }

    func fetchEpisode(id: Int) async throws -> AudioEpisode { throw Failure.unused }

    func fetchCustomNarrationEpisodes(limit: Int) async throws -> [AudioEpisode] {
        responses.count == 1 ? responses[0] : responses.removeFirst()
    }

    func streamResource(
        for episode: AudioEpisode
    ) async throws -> AuthorizedMediaResource { throw Failure.unused }

}
