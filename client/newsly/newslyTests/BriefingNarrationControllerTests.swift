import XCTest
@testable import newsly

@MainActor
final class BriefingNarrationControllerTests: XCTestCase {
    func testPrepareNarrationStoresPlayableManifestByLens() async throws {
        let service = MockBriefingService()
        let firstChapter = makeAudioEpisode(id: 42)
        service.narrationManifest = makeBriefingNarration(chapters: [firstChapter])
        let controller = makeController(service: service)

        let returned = try await controller.prepareNarration(for: "today")

        XCTAssertTrue(returned.playable)
        XCTAssertEqual(returned.chapters.map(\.id), [42])
        XCTAssertEqual(controller.narrationEpisode(for: "today")?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        XCTAssertTrue(service.narrationFetchEpisodeGroupIDs.isEmpty)
    }

    func testPrepareNarrationReturnsWhenFirstChapterIsReady() async throws {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(
            chapters: [
                makeAudioEpisode(id: 41),
                makeAudioEpisode(id: 42, status: .processing),
            ]
        )
        let controller = makeController(service: service)

        let narration = try await controller.prepareNarration(for: "today")

        XCTAssertTrue(narration.playable)
        XCTAssertEqual(narration.status, .processing)
        XCTAssertEqual(controller.narrationEpisode(for: "today")?.id, 41)
        XCTAssertTrue(service.narrationFetchEpisodeGroupIDs.isEmpty)
    }

    func testPendingNarrationPollsManifestUntilFirstChapterIsReady() async throws {
        let service = MockBriefingService()
        let pending = makeBriefingNarration(
            episodeGroupID: "group-42",
            chapters: [makeAudioEpisode(id: 42, status: .processing)]
        )
        let completed = makeBriefingNarration(
            episodeGroupID: "group-42",
            chapters: [makeAudioEpisode(id: 42)]
        )
        service.narrationManifest = pending
        service.narrationFetchResults = [.success(completed)]
        let controller = makeController(service: service)

        let narration = try await controller.prepareNarration(for: "today")

        XCTAssertTrue(narration.playable)
        XCTAssertEqual(service.narrationFetchEpisodeGroupIDs, ["group-42"])
    }

    func testPreparingLaterChapterPollsSameManifestAndSelectsChapter() async throws {
        let service = MockBriefingService()
        let initial = makeBriefingNarration(
            episodeGroupID: "group-42",
            chapters: [
                makeAudioEpisode(id: 41),
                makeAudioEpisode(id: 42, status: .processing),
            ]
        )
        let completed = makeBriefingNarration(
            episodeGroupID: "group-42",
            chapters: [makeAudioEpisode(id: 41), makeAudioEpisode(id: 42)]
        )
        service.narrationManifest = initial
        service.narrationFetchResults = [.success(completed)]
        let controller = makeController(service: service, pollMaxAttempts: 1)
        _ = try await controller.prepareNarration(for: "today")

        let chapter = try await controller.prepareNarrationChapter(at: 1, for: "today")

        XCTAssertEqual(chapter.id, 42)
        XCTAssertEqual(controller.narrationChapterIndex(for: "today"), 1)
        XCTAssertEqual(controller.narrationEpisode(for: "today")?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        XCTAssertEqual(service.narrationFetchEpisodeGroupIDs, ["group-42"])
    }

    func testAutomaticAdvanceRequiresCurrentFinishedChapterAndStopsAtEnd() async throws {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(
            chapters: [makeAudioEpisode(id: 41), makeAudioEpisode(id: 42)]
        )
        let controller = makeController(service: service)
        _ = try await controller.prepareNarration(for: "today")

        XCTAssertEqual(
            controller.nextNarrationChapterIndex(
                afterFinishedEpisodeID: 41,
                chapterIndex: 0,
                for: "today"
            ),
            1
        )
        XCTAssertNil(
            controller.nextNarrationChapterIndex(
                afterFinishedEpisodeID: 999,
                chapterIndex: 0,
                for: "today"
            )
        )

        _ = try await controller.prepareNarrationChapter(at: 1, for: "today")

        XCTAssertNil(
            controller.nextNarrationChapterIndex(
                afterFinishedEpisodeID: 42,
                chapterIndex: 1,
                for: "today"
            )
        )
    }

    func testNarrationFailureKeepsLoadedBriefingVisible() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: nil)
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        service.narrationError = NSError(
            domain: "BriefingNarrationTests",
            code: 404,
            userInfo: [NSLocalizedDescriptionKey: "Raw provider diagnostics"]
        )
        let viewModel = BriefingViewModel(service: service)
        let controller = viewModel.narrationController
        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }

        do {
            _ = try await controller.prepareNarration(for: "today")
            XCTFail("Expected narration request to fail")
        } catch {}

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertNil(controller.narration(for: "today"))
    }

    func testTerminalNarrationFailureRetainsEditionAndRetriesOriginalGroup() async throws {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(
            episodeGroupID: "original", chapters: [makeAudioEpisode(id: 41, status: .failed)]
        )
        service.narrationRetryResults = [.success(makeBriefingNarration(
            episodeGroupID: "original", chapters: [makeAudioEpisode(id: 41)]
        ))]
        let controller = makeController(service: service)

        do {
            _ = try await controller.prepareNarration(for: "today")
            XCTFail("Expected terminal narration failure")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .generationFailed)
        }
        XCTAssertEqual(controller.narration(for: "today")?.episodeGroupId, "original")

        let replacement = try await controller.prepareNarration(for: "today")

        XCTAssertEqual(replacement.chapters.first?.id, 41)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        XCTAssertEqual(service.narrationRetryEpisodeGroupIDs, ["original"])
    }

    func testNarrationTimeoutRetainsManifestAndResumesWithoutNewPost() async throws {
        let service = MockBriefingService()
        let pending = makeBriefingNarration(
            episodeGroupID: "group-41",
            chapters: [makeAudioEpisode(id: 41, status: .processing)]
        )
        service.narrationManifest = pending
        let controller = makeController(service: service, pollMaxAttempts: 2)

        do {
            _ = try await controller.prepareNarration(for: "today")
            XCTFail("Expected narration timeout")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .preparationTimedOut)
        }
        XCTAssertEqual(controller.narration(for: "today")?.episodeGroupId, "group-41")

        service.narrationFetchResults = [
            .success(
                makeBriefingNarration(
                    episodeGroupID: "group-41",
                    chapters: [makeAudioEpisode(id: 41)]
                )
            )
        ]
        let completed = try await controller.prepareNarration(for: "today")

        XCTAssertTrue(completed.playable)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
    }

    func testConcurrentNarrationPreparationUsesSingleRequestPerLens() async throws {
        let service = MockBriefingService()
        service.narrationRequestDelayNanoseconds = 100_000_000
        service.narrationManifest = makeBriefingNarration(
            chapters: [makeAudioEpisode(id: 42)]
        )
        let controller = makeController(service: service)

        async let first = controller.prepareNarration(for: "today")
        async let second = controller.prepareNarration(for: "today")
        let narrations = try await (first, second)

        XCTAssertEqual(narrations.0.chapters.first?.id, 42)
        XCTAssertEqual(narrations.1.chapters.first?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
    }

    func testCancellingOneWaiterKeepsSharedPreparationForOtherWaiter() async throws {
        let service = MockBriefingService()
        service.narrationRequestDelayNanoseconds = 100_000_000
        service.narrationManifest = makeBriefingNarration(
            chapters: [makeAudioEpisode(id: 42)]
        )
        let controller = makeController(service: service)

        let cancelledWaiter = Task { @MainActor in
            try await controller.prepareNarration(for: "today")
        }
        await waitFor { service.narrationLensKeys == ["today"] }
        let activeWaiter = Task { @MainActor in
            try await controller.prepareNarration(for: "today")
        }
        cancelledWaiter.cancel()

        do {
            _ = try await cancelledWaiter.value
            XCTFail("Expected cancelled waiter to throw")
        } catch where ClientFailure.classify(error) == .cancelled {}
        let completed = try await activeWaiter.value

        XCTAssertEqual(completed.chapters.first?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
    }

    func testCancellingOnlyWaiterReturnsPromptlyAndStopsPreparation() async throws {
        let service = MockBriefingService()
        service.narrationWaitsForCancellation = true
        let controller = makeController(service: service)
        let waiter = Task { @MainActor in
            try await controller.prepareNarration(for: "today")
        }
        await waitFor { service.narrationLensKeys == ["today"] }

        let cancellationCompleted = expectation(description: "cancelled waiter returns promptly")
        let cancellationResult = Task { @MainActor in
            defer { cancellationCompleted.fulfill() }
            do {
                _ = try await waiter.value
                return false
            } catch {
                return ClientFailure.classify(error) == .cancelled
            }
        }
        waiter.cancel()
        await fulfillment(of: [cancellationCompleted], timeout: 0.5)
        let didCancel = await cancellationResult.value
        XCTAssertTrue(didCancel)
        await waitFor { service.narrationCancellationCount == 1 }

        service.narrationWaitsForCancellation = false
        service.narrationManifest = makeBriefingNarration(
            chapters: [makeAudioEpisode(id: 42)]
        )
        let replacement = try await controller.prepareNarration(for: "today")

        XCTAssertEqual(replacement.chapters.first?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today", "today"])
    }

    func testPlaybackCompletionAdvancesThroughControllerAndStopsAtEnd() async throws {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(
            chapters: [
                makeAudioEpisode(id: 41, title: "First article", subtitle: "The Daily"),
                makeAudioEpisode(id: 42, title: "Second article"),
            ]
        )
        let playbackService = MockBriefingNarrationPlaybackService()
        let controller = makeController(
            service: service,
            playbackService: playbackService
        )

        await controller.playChapter(at: 0, for: "today")
        XCTAssertEqual(playbackService.playedTargets, [.audioEpisode(41)])
        XCTAssertEqual(
            playbackService.playedMetadata,
            [
                NarrationPlaybackMetadata(
                    title: "First article",
                    collectionTitle: "Today briefing",
                    subtitle: "The Daily",
                    artworkURL: nil,
                    chapterIndex: 0,
                    chapterCount: 2
                )
            ]
        )

        playbackService.finishCurrent()
        await waitFor { playbackService.playedTargets.count == 2 }

        XCTAssertEqual(playbackService.playedTargets, [.audioEpisode(41), .audioEpisode(42)])
        XCTAssertEqual(controller.narrationChapterIndex(for: "today"), 1)

        playbackService.finishCurrent()
        try? await Task.sleep(nanoseconds: 20_000_000)
        XCTAssertEqual(playbackService.playedTargets.count, 2)
    }

    func testSlowerEarlierLensCannotReplaceLatestPlaybackIntent() async {
        let service = MockBriefingService()
        service.narrationManifestsByLens = [
            "slow": makeBriefingNarration(
                episodeGroupID: "slow-group",
                chapters: [makeAudioEpisode(id: 41)]
            ),
            "latest": makeBriefingNarration(
                episodeGroupID: "latest-group",
                chapters: [makeAudioEpisode(id: 42)]
            ),
        ]
        service.narrationRequestWaitLensKeys = ["slow"]
        let playbackService = MockBriefingNarrationPlaybackService()
        let controller = makeController(
            service: service,
            playbackService: playbackService
        )

        let slowPlayback = Task { @MainActor in
            await controller.playChapter(at: 0, for: "slow")
        }
        await waitFor { service.narrationLensKeys.contains("slow") }

        await controller.playChapter(at: 0, for: "latest")
        XCTAssertEqual(playbackService.playedTargets, [.audioEpisode(42)])

        service.resumeNarrationRequest(lensKey: "slow")
        await slowPlayback.value

        XCTAssertEqual(playbackService.playedTargets, [.audioEpisode(42)])
        XCTAssertEqual(playbackService.speakingTarget, .audioEpisode(42))
    }

    func testPlayingUncachedLensStopsPreviousLensWhilePreparing() async throws {
        let service = MockBriefingService()
        service.narrationManifestsByLens = [
            "ai": makeBriefingNarration(lensKey: "ai", episodeGroupID: "ai-group",
                chapters: [makeAudioEpisode(id: 41), makeAudioEpisode(id: 42)]),
            "business": makeBriefingNarration(lensKey: "business", episodeGroupID: "business-group",
                chapters: [makeAudioEpisode(id: 51)]),
        ]
        service.narrationRequestWaitLensKeys = ["business"]
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        await controller.togglePlayback(for: "ai")

        // Looking at another lens's controls does not change the playing queue.
        XCTAssertNil(controller.session(for: "business").manifest)
        XCTAssertTrue(controller.isPlaying(lensKey: "ai"))
        let next = Task { @MainActor in await controller.togglePlayback(for: "business") }
        await waitFor { service.narrationLensKeys.contains("business") }
        XCTAssertNil(playback.speakingTarget)
        XCTAssertTrue(controller.session(for: "business").isPreparing)

        service.resumeNarrationRequest(lensKey: "business")
        await next.value
        XCTAssertEqual(playback.playedTargets, [.audioEpisode(41), .audioEpisode(51)])
        XCTAssertEqual(controller.narration(for: "ai")?.episodeGroupId, "ai-group")
        XCTAssertEqual(controller.narration(for: "business")?.episodeGroupId, "business-group")
        playback.finishCurrent()
        await Task.yield()
        XCTAssertNil(playback.speakingTarget)
        XCTAssertEqual(playback.playedTargets, [.audioEpisode(41), .audioEpisode(51)])
    }

    func testStopPlaybackClearsPlayingSessionAndKeepsManifest() async {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(
            chapters: [makeAudioEpisode(id: 41), makeAudioEpisode(id: 42)]
        )
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        await controller.togglePlayback(for: "today")
        XCTAssertTrue(controller.isPlaying(lensKey: "today"))

        controller.stopPlayback(for: "today")

        XCTAssertNil(playback.speakingTarget)
        XCTAssertFalse(controller.isPlaying(lensKey: "today"))
        XCTAssertFalse(controller.session(for: "today").isPreparing)
        XCTAssertNil(controller.session(for: "today").errorMessage)
        // The cached manifest survives so a later Play does not re-request it.
        XCTAssertEqual(controller.narration(for: "today")?.chapters.count, 2)
    }

    func testStopPlaybackWhilePreparingCancelsPreparationAndNeverPlays() async {
        let service = MockBriefingService()
        service.narrationWaitsForCancellation = true
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        let play = Task { @MainActor in await controller.togglePlayback(for: "today") }
        await waitFor { service.narrationLensKeys == ["today"] }
        XCTAssertTrue(controller.session(for: "today").isPreparing)

        controller.stopPlayback(for: "today")
        await play.value

        XCTAssertFalse(controller.session(for: "today").isPreparing)
        XCTAssertNil(controller.session(for: "today").errorMessage)
        XCTAssertEqual(service.narrationCancellationCount, 1)
        XCTAssertEqual(playback.playedTargets, [])
        XCTAssertNil(playback.speakingTarget)
    }

    func testEmptyLensShowsAnExplanationWithoutPlayingAudio() async {
        let service = MockBriefingService()
        service.narrationError = AudioEpisodeServiceError.emptyLens
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        await controller.togglePlayback(for: "empty")
        XCTAssertEqual(controller.session(for: "empty").errorMessage,
            "No unread stories to play in this lens.")
        XCTAssertFalse(controller.session(for: "empty").isPreparing)
        XCTAssertNil(controller.narration(for: "empty"))
        XCTAssertTrue(playback.playedTargets.isEmpty)
    }

    func testCompletedLensRequestsItsCurrentUnreadEdition() async {
        let service = MockBriefingService()
        service.narrationManifestsByLens["ai"] = makeBriefingNarration(
            lensKey: "ai", episodeGroupID: "old-edition", chapters: [makeAudioEpisode(id: 41)]
        )
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        await controller.togglePlayback(for: "ai")
        playback.finishCurrent()
        await Task.yield()
        service.narrationManifestsByLens["ai"] = makeBriefingNarration(
            lensKey: "ai", episodeGroupID: "new-edition", chapters: [makeAudioEpisode(id: 42)]
        )
        await controller.togglePlayback(for: "ai")
        XCTAssertEqual(service.narrationLensKeys, ["ai", "ai"])
        XCTAssertEqual(playback.playedTargets, [.audioEpisode(41), .audioEpisode(42)])
    }

    func testFailedChapterRetryDoesNotReuseAnIndexFromTheOldEdition() async throws {
        let service = MockBriefingService()
        service.narrationManifestsByLens["ai"] = makeBriefingNarration(
            lensKey: "ai", episodeGroupID: "old-edition", chapters: [
                makeAudioEpisode(id: 41), makeAudioEpisode(id: 42, status: .failed),
                makeAudioEpisode(id: 43)
            ]
        )
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        _ = try await controller.prepareNarration(for: "ai")
        // Chapter 41 was consumed, so the next create request returns only unread sources.
        service.narrationManifestsByLens["ai"] = makeBriefingNarration(
            lensKey: "ai", episodeGroupID: "new-edition", chapters: [
                makeAudioEpisode(id: 42), makeAudioEpisode(id: 43)
            ]
        )
        service.narrationRetryResults = [.success(makeBriefingNarration(
            lensKey: "ai", episodeGroupID: "old-edition", chapters: [
                makeAudioEpisode(id: 41), makeAudioEpisode(id: 42), makeAudioEpisode(id: 43)
            ]
        ))]
        await controller.playChapter(at: 1, for: "ai")
        XCTAssertEqual(service.narrationRetryEpisodeGroupIDs, ["old-edition"])
        XCTAssertEqual(service.narrationLensKeys, ["ai"])
        XCTAssertEqual(playback.playedTargets, [.audioEpisode(42)])
    }

    func testFreshEditionWaitsForReadMarksAndRespectsNewPlaybackIntent() async {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(chapters: [makeAudioEpisode(id: 41)])
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        await controller.togglePlayback(for: "today")
        var acknowledge: CheckedContinuation<Void, Never>?
        let readMarks = Task { @MainActor in
            await withCheckedContinuation { acknowledge = $0 }
        }
        await waitFor { acknowledge != nil }
        playback.finishCurrent(readMarks: readMarks)
        var nextStarted = false
        let next = Task { @MainActor in
            nextStarted = true
            await controller.togglePlayback(for: "today")
        }
        await waitFor { nextStarted }
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        // A newer explicit replay must win over the pending fresh-edition request.
        await controller.playChapter(at: 0, for: "today")
        acknowledge?.resume()
        await next.value
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        XCTAssertEqual(playback.playedTargets, [.audioEpisode(41), .audioEpisode(41)])
    }

    func testPauseResumeRetainsEditionUntilNaturalCompletion() async {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(chapters: [makeAudioEpisode(id: 41)])
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        await controller.togglePlayback(for: "today")
        await controller.togglePlayback(for: "today")
        XCTAssertFalse(playback.isSpeaking)
        await controller.togglePlayback(for: "today")
        XCTAssertTrue(playback.isSpeaking)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        playback.finishCurrent()
        service.narrationManifest = makeBriefingNarration(chapters: [makeAudioEpisode(id: 42)])
        await controller.togglePlayback(for: "today")
        XCTAssertEqual(service.narrationLensKeys, ["today", "today"])
        XCTAssertEqual(playback.playedTargets.last, .audioEpisode(42))
    }

    func testRetryRejectsReplacedChapterIdentities() async {
        let service = MockBriefingService()
        service.narrationManifest = makeBriefingNarration(chapters: [makeAudioEpisode(id: 41, status: .failed)])
        service.narrationRetryResults = [.success(makeBriefingNarration(chapters: [makeAudioEpisode(id: 42)]))]
        let playback = MockBriefingNarrationPlaybackService()
        let controller = makeController(service: service, playbackService: playback)
        await controller.togglePlayback(for: "today")
        await controller.togglePlayback(for: "today")
        XCTAssertTrue(playback.playedTargets.isEmpty)
        XCTAssertEqual(controller.narrationEpisode(for: "today")?.id, 41)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        XCTAssertEqual(service.narrationRetryEpisodeGroupIDs.count, 1)
    }

    private func makeController(
        service: MockBriefingService,
        playbackService: (any BriefingNarrationPlaybackControlling)? = nil,
        pollMaxAttempts: Int = 3
    ) -> BriefingNarrationController {
        BriefingNarrationController(
            briefingService: service,
            audioEpisodeService: MockBriefingAudioEpisodeService(),
            playbackService: playbackService ?? MockBriefingNarrationPlaybackService(),
            pollIntervalNanoseconds: 1_000_000,
            pollMaxAttempts: pollMaxAttempts
        )
    }

    private func waitFor(
        timeoutNanoseconds: UInt64 = 500_000_000,
        condition: @escaping @MainActor () -> Bool,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let startedAt = DispatchTime.now().uptimeNanoseconds
        while !condition() {
            if DispatchTime.now().uptimeNanoseconds - startedAt > timeoutNanoseconds {
                XCTFail("Condition was not met before timeout", file: file, line: line)
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }
}
