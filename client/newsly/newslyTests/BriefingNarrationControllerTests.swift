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

    func testTerminalNarrationFailureClearsManifestAndRetriesPost() async throws {
        let service = MockBriefingService()
        service.narrationManifests = [
            makeBriefingNarration(chapters: [makeAudioEpisode(id: 41, status: .failed)]),
            makeBriefingNarration(chapters: [makeAudioEpisode(id: 42)]),
        ]
        let controller = makeController(service: service)

        do {
            _ = try await controller.prepareNarration(for: "today")
            XCTFail("Expected terminal narration failure")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .generationFailed)
        }
        XCTAssertNil(controller.narration(for: "today"))

        let replacement = try await controller.prepareNarration(for: "today")

        XCTAssertEqual(replacement.chapters.first?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today", "today"])
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
        } catch where isNetworkCancellation(error) {}
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
                return isNetworkCancellation(error)
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
            chapters: [makeAudioEpisode(id: 41), makeAudioEpisode(id: 42)]
        )
        let playbackService = MockBriefingNarrationPlaybackService()
        let controller = makeController(
            service: service,
            playbackService: playbackService
        )

        await controller.playChapter(at: 0, for: "today")
        XCTAssertEqual(playbackService.playedTargets, [.audioEpisode(41)])

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
