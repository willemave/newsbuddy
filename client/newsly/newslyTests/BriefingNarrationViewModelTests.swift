import XCTest
@testable import newsly

@MainActor
final class BriefingNarrationViewModelTests: XCTestCase {
    func testPrepareNarrationStoresCompletedEpisodeByLens() async {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        let episode = makeAudioEpisode(id: 42)
        service.narrationEpisode = episode
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )

        let returned = try? await viewModel.prepareNarration(for: "today")

        XCTAssertEqual(returned?.id, 42)
        XCTAssertEqual(viewModel.narrationEpisode(for: "today")?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
    }

    func testPrepareNarrationFailureKeepsLoadedBriefingVisible() async {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: nil)
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        service.narrationError = NSError(
            domain: "BriefingNarrationTests",
            code: 404,
            userInfo: [NSLocalizedDescriptionKey: "Raw provider diagnostics"]
        )
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )
        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }

        do {
            _ = try await viewModel.prepareNarration(for: "today")
            XCTFail("Expected narration request to fail")
        } catch {}

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertNil(viewModel.narrationEpisode(for: "today"))
    }

    func testTerminalNarrationFailureClearsPendingEpisodeAndRetriesPost() async throws {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        service.narrationEpisodes = [
            makeAudioEpisode(id: 41, status: .processing),
            makeAudioEpisode(id: 42)
        ]
        audioService.waitResults = [.failure(AudioEpisodeServiceError.generationFailed)]
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )

        do {
            _ = try await viewModel.prepareNarration(for: "today")
            XCTFail("Expected terminal narration failure")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .generationFailed)
        }
        XCTAssertNil(viewModel.narrationEpisode(for: "today"))

        let replacement = try await viewModel.prepareNarration(for: "today")

        XCTAssertEqual(replacement.id, 42)
        XCTAssertEqual(viewModel.narrationEpisode(for: "today")?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today", "today"])
    }

    func testNarrationTimeoutRetainsPendingEpisodeAndResumesWithoutNewPost() async throws {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        service.narrationEpisode = makeAudioEpisode(id: 41, status: .processing)
        audioService.waitResults = [
            .failure(AudioEpisodeServiceError.preparationTimedOut),
            .success(makeAudioEpisode(id: 42))
        ]
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )

        do {
            _ = try await viewModel.prepareNarration(for: "today")
            XCTFail("Expected narration timeout")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .preparationTimedOut)
        }
        XCTAssertEqual(viewModel.narrationEpisode(for: "today")?.id, 41)
        XCTAssertTrue(viewModel.narrationEpisode(for: "today")?.isGenerating == true)

        let completed = try await viewModel.prepareNarration(for: "today")

        XCTAssertEqual(completed.id, 42)
        XCTAssertEqual(viewModel.narrationEpisode(for: "today")?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
        XCTAssertEqual(audioService.waitedEpisodeIDs, [41, 41])
    }

    func testNarrationCancellationDoesNotMutateEpisodeCache() async throws {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        service.narrationEpisodes = [
            makeAudioEpisode(id: 41, status: .processing),
            makeAudioEpisode(id: 42)
        ]
        audioService.waitResults = [.failure(CancellationError())]
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )

        do {
            _ = try await viewModel.prepareNarration(for: "today")
            XCTFail("Expected narration cancellation")
        } catch where isNetworkCancellation(error) {
            // Expected cancellation path.
        }
        XCTAssertNil(viewModel.narrationEpisode(for: "today"))

        let replacement = try await viewModel.prepareNarration(for: "today")

        XCTAssertEqual(replacement.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today", "today"])
    }

    func testConcurrentNarrationPreparationUsesSingleRequestPerLens() async throws {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        service.narrationRequestDelayNanoseconds = 100_000_000
        service.narrationEpisode = makeAudioEpisode(id: 42)
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )

        async let first = viewModel.prepareNarration(for: "today")
        async let second = viewModel.prepareNarration(for: "today")
        let episodes = try await (first, second)

        XCTAssertEqual([episodes.0.id, episodes.1.id], [42, 42])
        XCTAssertEqual(service.narrationLensKeys, ["today"])
    }

    func testCancellingOneNarrationWaiterKeepsSharedPreparationForOtherWaiter() async throws {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        service.narrationRequestDelayNanoseconds = 100_000_000
        service.narrationEpisode = makeAudioEpisode(id: 42)
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )

        let cancelledWaiter = Task { @MainActor in
            try await viewModel.prepareNarration(for: "today")
        }
        await waitFor { service.narrationLensKeys == ["today"] }
        let activeWaiter = Task { @MainActor in
            try await viewModel.prepareNarration(for: "today")
        }
        cancelledWaiter.cancel()

        do {
            _ = try await cancelledWaiter.value
            XCTFail("Expected cancelled waiter to throw")
        } catch where isNetworkCancellation(error) {
            // The shared preparation continues for the active waiter.
        }
        let completed = try await activeWaiter.value
        let cached = try await viewModel.prepareNarration(for: "today")

        XCTAssertEqual(completed.id, 42)
        XCTAssertEqual(cached.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
    }

    func testCancellingOnlyNarrationWaiterReturnsPromptlyAndStopsPreparation() async throws {
        let service = MockBriefingService()
        let audioService = MockBriefingAudioEpisodeService()
        service.narrationWaitsForCancellation = true
        let viewModel = BriefingViewModel(
            service: service,
            audioEpisodeService: audioService
        )
        let waiter = Task { @MainActor in
            try await viewModel.prepareNarration(for: "today")
        }
        await waitFor { service.narrationLensKeys == ["today"] }
        let waiterReturned = expectation(description: "cancelled narration waiter returned")
        var waiterError: Error?
        let observer = Task { @MainActor in
            do {
                _ = try await waiter.value
            } catch {
                waiterError = error
            }
            waiterReturned.fulfill()
        }

        waiter.cancel()

        await fulfillment(of: [waiterReturned], timeout: 0.5)
        XCTAssertTrue(waiterError.map(isNetworkCancellation) == true)
        await waitFor { service.narrationCancellationCount == 1 }

        service.narrationWaitsForCancellation = false
        service.narrationEpisode = makeAudioEpisode(id: 42)
        let replacement = try await viewModel.prepareNarration(for: "today")
        _ = await observer.result

        XCTAssertEqual(replacement.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today", "today"])
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
