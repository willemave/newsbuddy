import XCTest
@testable import newsly

@MainActor
final class LearningDeckReaderReliabilityTests: XCTestCase {
    func testActiveRegenerationKeepsOldViewerWhilePollingForReplacement() async {
        let oldURL = URL(string: "https://example.com/old-deck")!
        let newURL = URL(string: "https://example.com/new-deck")!
        let active = makeLearningDeck(
            viewerAvailable: true,
            latestSuccessfulRunId: 10,
            runStatus: .generating,
            runId: 11
        )
        let completed = makeLearningDeck(
            viewerAvailable: true,
            latestSuccessfulRunId: 11,
            runStatus: .completed,
            runId: 11
        )
        let service = MockLearningDeckService(
            fetchResults: [.success(active), .success(completed)],
            viewerURLs: [newURL]
        )
        let viewModel = makeReaderViewModel(deck: active, service: service)

        viewModel.prepareViewer(initialURL: oldURL)

        XCTAssertEqual(viewModel.resolvedViewerURL, oldURL)
        let replacedViewer = await waitUntil {
            viewModel.resolvedViewerURL == newURL
        }
        XCTAssertTrue(replacedViewer)
        XCTAssertGreaterThanOrEqual(service.fetchCallCount, 2)
    }

    func testViewerResolutionRetriesTransientFetchFailure() async {
        let ready = makeLearningDeck(
            viewerAvailable: true,
            latestSuccessfulRunId: 12,
            runStatus: .completed,
            runId: 12
        )
        let expectedURL = URL(string: "https://example.com/recovered-deck")!
        let service = MockLearningDeckService(
            fetchResults: [
                .failure(LearningDeckTestError.transient),
                .success(ready),
            ],
            viewerURLs: [expectedURL]
        )
        let viewModel = makeReaderViewModel(
            deck: makeLearningDeck(runStatus: .preparing),
            service: service
        )

        viewModel.prepareViewer(initialURL: nil)

        let resolved = await waitUntil { viewModel.resolvedViewerURL == expectedURL }
        XCTAssertTrue(resolved)
        XCTAssertFalse(viewModel.viewerResolutionFailed)
        XCTAssertGreaterThanOrEqual(service.fetchCallCount, 2)
    }

    func testViewerPollingWindowDoesNotTurnActiveGenerationIntoFailure() async {
        let active = makeLearningDeck(runStatus: .generating)
        let service = MockLearningDeckService(fetchResults: [.success(active)])
        let viewModel = LearningDeckReaderViewModel(
            deck: active,
            chatService: NoopLearningDeckReaderChatService(),
            deckService: service,
            viewerPollIntervalNanoseconds: 1_000_000,
            viewerPollAttemptLimit: 1
        )

        viewModel.prepareViewer(initialURL: nil)

        let stoppedPolling = await waitUntil { !viewModel.isResolvingViewer }
        XCTAssertTrue(stoppedPolling)
        XCTAssertFalse(viewModel.viewerResolutionFailed)
        XCTAssertEqual(viewModel.generationStatusLabel, "Taking longer than expected")
    }

    private func makeReaderViewModel(
        deck: LearningDeck,
        service: MockLearningDeckService
    ) -> LearningDeckReaderViewModel {
        LearningDeckReaderViewModel(
            deck: deck,
            chatService: NoopLearningDeckReaderChatService(),
            deckService: service,
            viewerPollIntervalNanoseconds: 1_000_000,
            viewerPollAttemptLimit: 5
        )
    }
}

@MainActor
final class LearningDecksViewModelTests: XCTestCase {
    func testListPollingContinuesAfterTransientFailure() async {
        let active = makeLearningDeck(runStatus: .preparing)
        let ready = makeLearningDeck(
            viewerAvailable: true,
            latestSuccessfulRunId: 2,
            runStatus: .completed,
            runId: 2
        )
        let service = MockLearningDeckService(
            listedDecks: [active],
            fetchResults: [
                .failure(LearningDeckTestError.transient),
                .success(ready),
            ]
        )
        let viewModel = LearningDecksViewModel(
            service: service,
            pollingIntervalNanoseconds: 1_000_000,
            pollingAttemptLimit: 5
        )

        await viewModel.load()

        let becameReady = await waitUntil {
            viewModel.decks.first?.hasActiveLatestRun == false
                && viewModel.decks.first?.viewerAvailable == true
        }
        XCTAssertTrue(becameReady)
        XCTAssertGreaterThanOrEqual(service.fetchCallCount, 2)
    }

    func testRegenerateReusesContentAndExistingFocus() async {
        let original = makeLearningDeck(
            sourceURL: "https://example.com/source",
            sourceContentId: 77,
            viewerAvailable: true,
            latestSuccessfulRunId: 3,
            runStatus: .completed,
            runId: 3,
            interestsPrompt: "Focus on the tradeoffs"
        )
        let replacement = makeLearningDeck(
            sourceURL: original.sourceURL,
            sourceContentId: original.sourceContentId,
            viewerAvailable: true,
            latestSuccessfulRunId: 3,
            runStatus: .preparing,
            runId: 4,
            interestsPrompt: "Focus on the tradeoffs"
        )
        let service = MockLearningDeckService(
            listedDecks: [original],
            createResult: replacement
        )
        let viewModel = LearningDecksViewModel(service: service)
        await viewModel.load()

        let result = await viewModel.regenerate(original)

        XCTAssertEqual(result?.id, original.id)
        XCTAssertEqual(service.createRequests.count, 1)
        XCTAssertEqual(service.createRequests.first?.contentId, 77)
        XCTAssertNil(service.createRequests.first?.url)
        XCTAssertEqual(service.createRequests.first?.interestsPrompt, "Focus on the tradeoffs")
    }
}

private enum LearningDeckTestError: LocalizedError {
    case transient
    case unimplemented

    var errorDescription: String? {
        switch self {
        case .transient:
            "Connection interrupted"
        case .unimplemented:
            "Not implemented in this test"
        }
    }
}

private final class MockLearningDeckService: LearningDeckServicing {
    struct CreateRequest {
        let contentId: Int?
        let newsItemId: Int?
        let url: String?
        let interestsPrompt: String?
    }

    private let listedDecks: [LearningDeck]
    private var fetchResults: [Result<LearningDeck, Error>]
    private let createResult: LearningDeck?
    private var viewerURLs: [URL]

    private(set) var fetchCallCount = 0
    private(set) var createRequests: [CreateRequest] = []

    init(
        listedDecks: [LearningDeck] = [],
        fetchResults: [Result<LearningDeck, Error>] = [],
        createResult: LearningDeck? = nil,
        viewerURLs: [URL] = []
    ) {
        self.listedDecks = listedDecks
        self.fetchResults = fetchResults
        self.createResult = createResult
        self.viewerURLs = viewerURLs
    }

    func listDecks() async throws -> LearningDeckListResponse {
        LearningDeckListResponse(decks: listedDecks)
    }

    func fetchDeck(id: Int) async throws -> LearningDeck {
        fetchCallCount += 1
        guard !fetchResults.isEmpty else {
            throw LearningDeckTestError.unimplemented
        }
        let result = fetchResults.count == 1 ? fetchResults[0] : fetchResults.removeFirst()
        return try result.get()
    }

    func createDeck(
        contentId: Int?,
        newsItemId: Int?,
        url: String?,
        interestsPrompt: String?
    ) async throws -> LearningDeck {
        createRequests.append(
            CreateRequest(
                contentId: contentId,
                newsItemId: newsItemId,
                url: url,
                interestsPrompt: interestsPrompt
            )
        )
        guard let createResult else {
            throw LearningDeckTestError.unimplemented
        }
        return createResult
    }

    func viewerURL(deckId: Int) async throws -> URL {
        guard !viewerURLs.isEmpty else {
            throw LearningDeckTestError.unimplemented
        }
        return viewerURLs.count == 1 ? viewerURLs[0] : viewerURLs.removeFirst()
    }

    func sourceNotesURL(deckId: Int) async throws -> URL {
        throw LearningDeckTestError.unimplemented
    }

    func enableShare(deckId: Int) async throws -> LearningDeckShareResponse {
        throw LearningDeckTestError.unimplemented
    }

    func disableShare(deckId: Int) async throws -> LearningDeckShareResponse {
        throw LearningDeckTestError.unimplemented
    }

    func deleteDeck(deckId: Int) async throws {
        throw LearningDeckTestError.unimplemented
    }
}

private final class NoopLearningDeckReaderChatService: LearningDeckReaderChatServicing {
    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        throw LearningDeckTestError.unimplemented
    }

    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        throw LearningDeckTestError.unimplemented
    }
}

private func makeLearningDeck(
    id: Int = 1,
    sourceURL: String? = nil,
    sourceContentId: Int? = 7,
    viewerAvailable: Bool = false,
    latestSuccessfulRunId: Int? = nil,
    runStatus: LearningDeckRunStatus? = nil,
    runId: Int = 1,
    interestsPrompt: String? = "Existing focus"
) -> LearningDeck {
    let createdAt = ServerDate.parse("2026-04-01T10:00:00Z")!
    let latestRun = runStatus.map { status in
        LearningDeckRun(
            id: runId,
            status: status,
            interestsPrompt: interestsPrompt,
            timeline: [],
            errorMessage: nil,
            startedAt: createdAt,
            completedAt: status.isActive ? nil : createdAt,
            createdAt: createdAt,
            updatedAt: createdAt
        )
    }
    return LearningDeck(
        id: id,
        title: "Deck",
        sourceKind: .content,
        sourceURL: sourceURL,
        sourceContentId: sourceContentId,
        sourceTitle: "Source",
        sourceMetadata: [:],
        status: runStatus,
        shareEnabled: false,
        viewerAvailable: viewerAvailable,
        sourceNotesAvailable: false,
        latestSuccessfulRunId: latestSuccessfulRunId,
        latestRun: latestRun,
        createdAt: createdAt,
        updatedAt: createdAt
    )
}

@MainActor
private func waitUntil(_ condition: () -> Bool) async -> Bool {
    for _ in 0..<200 {
        if condition() {
            return true
        }
        try? await Task.sleep(nanoseconds: 1_000_000)
    }
    return condition()
}
