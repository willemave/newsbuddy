import XCTest
@testable import newsly

@MainActor
final class CustomNarrationCreationViewModelTests: XCTestCase {
    func testGenerationFailureUsesTypedSafeMessage() async {
        let audioService = MockCustomNarrationAudioService()
        let toastPresenter = MockCustomNarrationToastPresenter()
        audioService.createdEpisode = makeCustomNarrationEpisode(status: .processing)
        audioService.waitError = AudioEpisodeServiceError.generationFailed
        audioService.fetchedEpisode = makeCustomNarrationEpisode(
            status: .failed,
            errorMessage: "Private provider diagnostics"
        )
        let viewModel = CustomNarrationCreationViewModel(
            audioService: audioService,
            toastPresenter: toastPresenter
        )

        _ = await viewModel.create(from: makeSelection())
        await viewModel.pollIfNeeded(isActive: true)

        XCTAssertEqual(
            viewModel.errorMessage,
            AudioEpisodeServiceError.generationFailed.userFacingMessage
        )
        XCTAssertEqual(
            toastPresenter.errorMessages,
            [AudioEpisodeServiceError.generationFailed.userFacingMessage]
        )
        XCTAssertFalse(viewModel.errorMessage?.contains("provider") == true)
    }

    func testCreateFailureUsesSafeFallbackInsteadOfRawDiagnostics() async {
        let audioService = MockCustomNarrationAudioService()
        let toastPresenter = MockCustomNarrationToastPresenter()
        audioService.createError = NSError(
            domain: "Provider",
            code: 404,
            userInfo: [NSLocalizedDescriptionKey: "Raw model diagnostics"]
        )
        let viewModel = CustomNarrationCreationViewModel(
            audioService: audioService,
            toastPresenter: toastPresenter
        )

        let created = await viewModel.create(from: makeSelection())

        XCTAssertFalse(created)
        XCTAssertEqual(
            viewModel.errorMessage,
            AudioEpisodeServiceError.generationFailed.userFacingMessage
        )
        XCTAssertEqual(
            toastPresenter.errorMessages,
            ["Failed to create narration: \(AudioEpisodeServiceError.generationFailed.userFacingMessage)"]
        )
    }
}

@MainActor
private final class MockCustomNarrationAudioService: CustomNarrationAudioServicing {
    var createdEpisode = makeCustomNarrationEpisode()
    var createError: Error?
    var waitError: Error?
    var fetchedEpisode: AudioEpisode?

    func createCustomNarrationEpisode(
        contentIds: [Int],
        newsItemIds: [Int],
        title: String?,
        markSourceContentReadOnPlay: Bool,
        delivery: AudioEpisodeDelivery
    ) async throws -> AudioEpisode {
        if let createError { throw createError }
        return createdEpisode
    }

    func waitForCompletedEpisode(
        _ episode: AudioEpisode,
        pollIntervalNanoseconds: UInt64,
        maxAttempts: Int
    ) async throws -> AudioEpisode {
        if let waitError { throw waitError }
        return episode
    }

    func fetchEpisode(id: Int) async throws -> AudioEpisode {
        fetchedEpisode ?? createdEpisode
    }

    func fetchCustomNarrationEpisodes(limit: Int) async throws -> [AudioEpisode] {
        []
    }

    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        throw AudioEpisodeServiceError.missingStreamResource
    }

    func enableEpisodeShare(id: Int) async throws -> AudioEpisodeShareResponse {
        AudioEpisodeShareResponse(shareEnabled: false)
    }
}

@MainActor
private final class MockCustomNarrationToastPresenter: ToastPresenting {
    private(set) var errorMessages: [String] = []

    func show(_ message: String, type: ToastType, duration: TimeInterval) {}

    func showError(_ message: String) {
        errorMessages.append(message)
    }

    func showSuccess(_ message: String) {}
}

private func makeSelection() -> CustomNarrationSourceSelection {
    CustomNarrationSourceSelection(
        contentIds: [1],
        newsItemIds: [],
        markSourceContentReadOnPlay: false
    )
}

private func makeCustomNarrationEpisode(
    status: APIAudioEpisodeStatus = .completed,
    errorMessage: String? = nil
) -> AudioEpisode {
    AudioEpisode(
        id: 41,
        kind: .custom_narration,
        status: status,
        title: "Narration",
        sourceCount: 1,
        sourceTitles: ["Long report"],
        errorMessage: errorMessage,
        createdAt: Date(timeIntervalSince1970: 1_800_000_200)
    )
}
