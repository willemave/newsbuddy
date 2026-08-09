import XCTest
@testable import newsly

@MainActor
final class CustomNarrationLibraryViewModelTests: XCTestCase {
    func testGeneratingEpisodePollsUntilItCompletes() async {
        let processing = makeEpisode(id: 41, status: .processing)
        let completed = makeEpisode(id: 41, status: .completed)
        let service = MockCustomNarrationLibraryService(
            listResponses: [[processing]],
            fetchResponses: [processing, completed]
        )
        let viewModel = makeViewModel(service: service, pollingAttemptLimit: 5)

        await viewModel.load()

        let didComplete = await waitUntil {
            viewModel.episodes.first?.status == .completed
        }
        XCTAssertTrue(didComplete)
        XCTAssertEqual(service.fetchedEpisodeIDs, [41, 41])
    }

    func testOlderLoadCannotReplaceTheNewestLoadedEpisodes() async {
        let processing = makeEpisode(id: 41, status: .processing)
        let completed = makeEpisode(id: 41, status: .completed)
        let service = MockCustomNarrationLibraryService(
            listResponses: [[processing], [completed]]
        )
        let viewModel = makeViewModel(service: service)

        service.pauseNextListResponse()
        let olderLoad = Task { await viewModel.load() }
        let didPause = await waitUntil { service.listResponsePaused }
        XCTAssertTrue(didPause)

        await viewModel.load()
        service.resumeListResponse()
        await olderLoad.value

        XCTAssertEqual(viewModel.episodes.first?.status, .completed)
        XCTAssertFalse(viewModel.isLoading)
    }

    func testRetryReplacesFailedEpisodeAndUsesBackgroundDelivery() async {
        let failed = makeEpisode(
            id: 41,
            status: .failed,
            sourceContentIDs: [7]
        )
        let replacement = makeEpisode(
            id: 42,
            status: .completed,
            sourceContentIDs: [7]
        )
        let service = MockCustomNarrationLibraryService(
            listResponses: [[failed]],
            createResponse: replacement
        )
        let viewModel = makeViewModel(service: service)
        await viewModel.load()

        await viewModel.retry(failed)

        XCTAssertEqual(viewModel.episodes.map(\.id), [42])
        XCTAssertEqual(service.createDeliveries, [.background])
    }

    func testRetryReplacementSurvivesStaleFollowUpLoad() async {
        let failed = makeEpisode(
            id: 41,
            status: .failed,
            sourceContentIDs: [7]
        )
        let replacement = makeEpisode(
            id: 42,
            status: .completed,
            sourceContentIDs: [7]
        )
        let service = MockCustomNarrationLibraryService(
            listResponses: [[failed], [failed]],
            createResponse: replacement
        )
        let viewModel = makeViewModel(service: service)
        await viewModel.load()

        await viewModel.retry(failed)
        await viewModel.load()

        XCTAssertEqual(viewModel.episodes.map(\.id), [42])
    }

    private func makeViewModel(
        service: MockCustomNarrationLibraryService,
        pollingAttemptLimit: Int = 2
    ) -> CustomNarrationLibraryViewModel {
        let defaults = UserDefaults(suiteName: "CustomNarrationTests.\(UUID().uuidString)")!
        let playback = NarrationPlaybackService(
            preferenceStore: NarrationPlaybackPreferenceStore(
                defaults: defaults,
                storageKey: "playbackRate"
            )
        )
        let badgeStore = BadgeStatsStore(
            fetchStats: {
                APIBadgeStatsResponse(
                    unread: APIUnreadCountsResponse(article: 0, podcast: 0, news: 0),
                    processing: APIProcessingCountResponse(
                        processingCount: 0,
                        longFormCount: 0,
                        newsCount: 0,
                        newsCrawlCount: 0
                    )
                )
            },
            isApplicationActive: { false }
        )
        return CustomNarrationLibraryViewModel(
            playbackService: playback,
            audioService: service,
            badgeStatsStore: badgeStore,
            toastPresenter: NoopNarrationToastPresenter(),
            pollingIntervalNanoseconds: 1_000_000,
            pollingAttemptLimit: pollingAttemptLimit
        )
    }

    private func makeEpisode(
        id: Int,
        status: APIAudioEpisodeStatus,
        sourceContentIDs: [Int] = []
    ) -> AudioEpisode {
        AudioEpisode(
            id: id,
            kind: .custom_narration,
            status: status,
            title: "Custom narration",
            sourceContentIds: sourceContentIDs,
            sourceCount: sourceContentIDs.count,
            sourceTitles: ["Source"],
            errorMessage: status == .failed ? "Generation failed" : nil,
            createdAt: Date(timeIntervalSince1970: 1_800_000_200)
        )
    }

    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(1))
        }
        return condition()
    }
}

@MainActor
private final class MockCustomNarrationLibraryService: CustomNarrationLibraryServicing {
    private var listResponses: [[AudioEpisode]]
    private var fetchResponses: [AudioEpisode]
    private let createResponse: AudioEpisode?
    private var shouldPauseNextList = false
    private var shouldKeepListPaused = false

    private(set) var fetchedEpisodeIDs: [Int] = []
    private(set) var createDeliveries: [AudioEpisodeDelivery] = []

    var listResponsePaused: Bool {
        shouldKeepListPaused
    }

    init(
        listResponses: [[AudioEpisode]],
        fetchResponses: [AudioEpisode] = [],
        createResponse: AudioEpisode? = nil
    ) {
        self.listResponses = listResponses
        self.fetchResponses = fetchResponses
        self.createResponse = createResponse
    }

    func createCustomNarrationEpisode(
        contentIds: [Int],
        newsItemIds: [Int],
        title: String?,
        markSourceContentReadOnPlay: Bool,
        delivery: AudioEpisodeDelivery
    ) async throws -> AudioEpisode {
        _ = (contentIds, newsItemIds, title, markSourceContentReadOnPlay)
        createDeliveries.append(delivery)
        guard let createResponse else { throw TestError.unexpectedCall }
        return createResponse
    }

    func fetchEpisode(id: Int) async throws -> AudioEpisode {
        fetchedEpisodeIDs.append(id)
        guard !fetchResponses.isEmpty else { throw TestError.unexpectedCall }
        return fetchResponses.count == 1 ? fetchResponses[0] : fetchResponses.removeFirst()
    }

    func fetchCustomNarrationEpisodes(limit: Int) async throws -> [AudioEpisode] {
        _ = limit
        guard !listResponses.isEmpty else { throw TestError.unexpectedCall }
        let response = listResponses.count == 1 ? listResponses[0] : listResponses.removeFirst()
        if shouldPauseNextList {
            shouldPauseNextList = false
            shouldKeepListPaused = true
            while shouldKeepListPaused {
                try await Task.sleep(for: .milliseconds(1))
            }
        }
        return response
    }

    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        throw TestError.unexpectedCall
    }

    func enableEpisodeShare(id: Int) async throws -> AudioEpisodeShareResponse {
        throw TestError.unexpectedCall
    }

    func pauseNextListResponse() {
        shouldPauseNextList = true
    }

    func resumeListResponse() {
        shouldKeepListPaused = false
    }

    enum TestError: Error {
        case unexpectedCall
    }
}

@MainActor
private final class NoopNarrationToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
