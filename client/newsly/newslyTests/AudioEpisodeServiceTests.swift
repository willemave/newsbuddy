import XCTest
@testable import newsly

final class AudioEpisodeServiceTests: XCTestCase {
    func testFailedEpisodeUsesSafeTypedError() async {
        let providerMessage = "status_code: 404, model_name: retired-preview-model"
        let episode = makeEpisode(
            id: 41,
            status: .failed,
            errorMessage: providerMessage
        )
        let poller = AudioEpisodePoller { _ in episode }

        do {
            _ = try await poller.waitForCompletedEpisode(
                episode,
                pollIntervalNanoseconds: 0,
                maxAttempts: 1
            )
            XCTFail("Expected failed episode to throw")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .generationFailed)
            XCTAssertEqual(
                error.localizedDescription,
                AudioEpisodeServiceError.generationFailed.userFacingMessage
            )
            XCTAssertFalse(error.localizedDescription.contains(providerMessage))
        }
    }

    func testPollerReturnsCompletionFromFinalAllowedFetch() async throws {
        let pending = makeEpisode(id: 41, status: .processing)
        let completed = makeEpisode(id: 41)
        var fetchedEpisodeIDs: [Int] = []
        let poller = AudioEpisodePoller { episodeID in
            fetchedEpisodeIDs.append(episodeID)
            return completed
        }

        let result = try await poller.waitForCompletedEpisode(
            pending,
            pollIntervalNanoseconds: 0,
            maxAttempts: 1
        )

        XCTAssertEqual(result.status, .completed)
        XCTAssertEqual(fetchedEpisodeIDs, [41])
    }

    func testPollerThrowsFailureFromFinalAllowedFetch() async {
        let pending = makeEpisode(id: 41, status: .processing)
        let failed = makeEpisode(
            id: 41,
            status: .failed,
            errorMessage: "Private provider diagnostics"
        )
        var fetchedEpisodeIDs: [Int] = []
        let poller = AudioEpisodePoller { episodeID in
            fetchedEpisodeIDs.append(episodeID)
            return failed
        }

        do {
            _ = try await poller.waitForCompletedEpisode(
                pending,
                pollIntervalNanoseconds: 0,
                maxAttempts: 1
            )
            XCTFail("Expected final fetched episode to fail")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .generationFailed)
        }

        XCTAssertEqual(fetchedEpisodeIDs, [41])
    }

    func testMissingStreamResourceUsesTypedError() async {
        let episode = makeEpisode(id: 41)

        do {
            _ = try await AudioEpisodeService.shared.streamResource(for: episode)
            XCTFail("Expected missing stream resource to throw")
        } catch {
            XCTAssertEqual(error as? AudioEpisodeServiceError, .missingStreamResource)
            XCTAssertEqual(
                error.localizedDescription,
                AudioEpisodeServiceError.missingStreamResource.userFacingMessage
            )
        }
    }
}

private func makeEpisode(
    id: Int,
    status: APIAudioEpisodeStatus = .completed,
    errorMessage: String? = nil
) -> AudioEpisode {
    AudioEpisode(
        id: id,
        kind: .briefing_narration,
        status: status,
        title: "Briefing",
        sourceContentId: nil,
        sourceCount: 1,
        sourceTitles: ["Long report"],
        durationSeconds: nil,
        audioUrl: nil,
        streamUrl: nil,
        scriptText: nil,
        errorMessage: errorMessage,
        createdAt: Date(timeIntervalSince1970: 1_800_000_200),
        updatedAt: nil
    )
}
