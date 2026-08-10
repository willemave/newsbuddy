import XCTest
@testable import newsly

final class LearningDeckURLValidatorTests: XCTestCase {
    func testAcceptsSameOriginRemoteHTTPSURL() throws {
        let apiBaseURL = URL(string: "https://racknerd.example.com:443")!

        let resolved = try LearningDeckURLValidator.validate(
            "https://racknerd.example.com/learning/signed/opaque-token/",
            apiBaseURL: apiBaseURL
        )

        XCTAssertEqual(
            resolved.absoluteString,
            "https://racknerd.example.com/learning/signed/opaque-token/"
        )
    }

    func testAcceptsCanonicalRemoteHTTPSURLWhenAPIUsesAnAlias() throws {
        let apiBaseURL = URL(string: "https://api-alias.example.com:443")!

        let resolved = try LearningDeckURLValidator.validate(
            "https://public.example.com/learning/signed/opaque-token/",
            apiBaseURL: apiBaseURL
        )

        XCTAssertEqual(
            resolved.absoluteString,
            "https://public.example.com/learning/signed/opaque-token/"
        )
    }

    func testAcceptsSignedSourceNotesURL() throws {
        let apiBaseURL = URL(string: "https://api-alias.example.com:443")!

        let resolved = try LearningDeckURLValidator.validate(
            "https://public.example.com/learning/signed/opaque-token/source-notes",
            apiBaseURL: apiBaseURL
        )

        XCTAssertEqual(
            resolved.absoluteString,
            "https://public.example.com/learning/signed/opaque-token/source-notes"
        )
    }

    func testRejectsRemoteHTTPURLInsteadOfRepairingIt() {
        let apiBaseURL = URL(string: "https://racknerd.example.com:443")!

        XCTAssertThrowsError(
            try LearningDeckURLValidator.validate(
                "http://racknerd.example.com/learning/signed/opaque-token/",
                apiBaseURL: apiBaseURL
            )
        ) { error in
            XCTAssertEqual(
                error as? LearningDeckURLValidationError,
                .insecureRemoteURL
            )
        }
    }

    func testAllowsLoopbackHTTPForLocalDevelopment() throws {
        let apiBaseURL = URL(string: "http://localhost:8000")!

        let resolved = try LearningDeckURLValidator.validate(
            "http://127.0.0.1:8000/learning/signed/local-token/",
            apiBaseURL: apiBaseURL
        )

        XCTAssertEqual(
            resolved.absoluteString,
            "http://127.0.0.1:8000/learning/signed/local-token/"
        )
    }

    func testDoesNotTreatHostnameBeginningWith127AsLoopback() {
        let apiBaseURL = URL(string: "http://localhost:8000")!

        XCTAssertThrowsError(
            try LearningDeckURLValidator.validate(
                "http://127.attacker.example/learning/signed/opaque-token/",
                apiBaseURL: apiBaseURL
            )
        ) { error in
            XCTAssertEqual(
                error as? LearningDeckURLValidationError,
                .insecureRemoteURL
            )
        }
    }

    func testRejectsNonLearningPath() {
        let apiBaseURL = URL(string: "https://racknerd.example.com:443")!

        XCTAssertThrowsError(
            try LearningDeckURLValidator.validate(
                "https://racknerd.example.com/not-a-deck",
                apiBaseURL: apiBaseURL
            )
        ) { error in
            XCTAssertEqual(error as? LearningDeckURLValidationError, .invalidURL)
        }
    }

    func testRejectsRemoteLoopbackURL() {
        let apiBaseURL = URL(string: "https://racknerd.example.com")!

        XCTAssertThrowsError(
            try LearningDeckURLValidator.validate(
                "https://127.0.0.1/learning/signed/opaque-token/",
                apiBaseURL: apiBaseURL
            )
        ) { error in
            XCTAssertEqual(error as? LearningDeckURLValidationError, .insecureRemoteURL)
        }
    }

    func testRejectsSignedPrefixWithoutAToken() {
        let apiBaseURL = URL(string: "https://racknerd.example.com")!

        XCTAssertThrowsError(
            try LearningDeckURLValidator.validate(
                "https://racknerd.example.com/learning/signed/",
                apiBaseURL: apiBaseURL
            )
        ) { error in
            XCTAssertEqual(error as? LearningDeckURLValidationError, .invalidURL)
        }
    }

    func testRejectsQueryOrFragmentOnSignedURL() {
        let apiBaseURL = URL(string: "https://racknerd.example.com")!

        for suffix in ["?redirect=https://attacker.example", "#fragment"] {
            XCTAssertThrowsError(
                try LearningDeckURLValidator.validate(
                    "https://racknerd.example.com/learning/signed/opaque-token/\(suffix)",
                    apiBaseURL: apiBaseURL
                )
            ) { error in
                XCTAssertEqual(error as? LearningDeckURLValidationError, .invalidURL)
            }
        }
    }

    func testRejectsUnexpectedPathBelowSignedToken() {
        let apiBaseURL = URL(string: "https://racknerd.example.com")!

        XCTAssertThrowsError(
            try LearningDeckURLValidator.validate(
                "https://racknerd.example.com/learning/signed/opaque-token/redirect",
                apiBaseURL: apiBaseURL
            )
        ) { error in
            XCTAssertEqual(error as? LearningDeckURLValidationError, .invalidURL)
        }
    }
}

final class LearningDeckWebLogOriginTests: XCTestCase {
    func testLogOriginExcludesSignedPath() {
        let signedURL = URL(
            string: "http://racknerd.example.com/learning/signed/sensitive-token/"
        )!

        let origin = LearningDeckWebLogOrigin(url: signedURL)

        XCTAssertEqual(origin.scheme, "http")
        XCTAssertEqual(origin.host, "racknerd.example.com")
    }
}

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
            messageCompletionRegistry: ChatMessageCompletionRegistry(
                statusService: NoopLearningDeckReaderChatService()
            ),
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

    func testViewerResolutionStopsRetryingTerminalURLFailure() async {
        let ready = makeLearningDeck(
            viewerAvailable: true,
            latestSuccessfulRunId: 12,
            runStatus: .completed,
            runId: 12
        )
        let service = MockLearningDeckService(
            fetchResults: [.success(ready)],
            viewerError: LearningDeckURLValidationError.insecureRemoteURL
        )
        let viewModel = makeReaderViewModel(
            deck: makeLearningDeck(runStatus: .preparing),
            service: service
        )

        viewModel.prepareViewer(initialURL: nil)

        let stoppedPolling = await waitUntil {
            !viewModel.isResolvingViewer && viewModel.viewerResolutionFailed
        }
        XCTAssertTrue(stoppedPolling)
        XCTAssertEqual(service.fetchCallCount, 1)
        XCTAssertEqual(service.viewerCallCount, 1)
        XCTAssertFalse(viewModel.canRetryGeneration)
        XCTAssertEqual(
            viewModel.generationNote,
            LearningDeckURLValidationError.insecureRemoteURL.localizedDescription
        )

        viewModel.retryAfterViewerFailure()

        let retriedViewerURL = await waitUntil { service.viewerCallCount == 2 }
        XCTAssertTrue(retriedViewerURL)
        XCTAssertEqual(service.retryCallCount, 0)
    }

    func testFailedGenerationRetryCreatesOneAttemptAndResolvesViewer() async {
        let failed = makeLearningDeck(runStatus: .failed)
        let retried = makeLearningDeck(runStatus: .queued, runId: 2)
        let completed = makeLearningDeck(
            viewerAvailable: true,
            latestSuccessfulRunId: 2,
            runStatus: .completed,
            runId: 2
        )
        let expectedURL = URL(string: "https://example.com/retried-deck")!
        let service = MockLearningDeckService(
            fetchResults: [.success(failed), .success(completed)],
            retryResults: [.success(retried)],
            viewerURLs: [expectedURL]
        )
        let viewModel = makeReaderViewModel(
            deck: makeLearningDeck(runStatus: .preparing),
            service: service
        )

        viewModel.prepareViewer(initialURL: nil)

        let showedGenerationFailure = await waitUntil {
            viewModel.viewerResolutionFailed && viewModel.canRetryGeneration
        }
        XCTAssertTrue(showedGenerationFailure)

        viewModel.retryAfterViewerFailure()
        viewModel.retryAfterViewerFailure()

        let resolved = await waitUntil { viewModel.resolvedViewerURL == expectedURL }
        XCTAssertTrue(resolved)
        XCTAssertEqual(service.retryCallCount, 1)
        XCTAssertFalse(viewModel.viewerResolutionFailed)
        XCTAssertFalse(viewModel.isRetryingGeneration)
    }

    private func makeReaderViewModel(
        deck: LearningDeck,
        service: MockLearningDeckService
    ) -> LearningDeckReaderViewModel {
        LearningDeckReaderViewModel(
            deck: deck,
            chatService: NoopLearningDeckReaderChatService(),
            messageCompletionRegistry: ChatMessageCompletionRegistry(
                statusService: NoopLearningDeckReaderChatService()
            ),
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

    func testCreatedDeckIsNotDroppedByRefreshThatStartedBeforeCreation() async {
        let existing = makeLearningDeck(id: 1, runStatus: .completed)
        let created = makeLearningDeck(id: 2, runStatus: .completed)
        let service = MockLearningDeckService(
            listedDeckResponses: [[existing], [existing]],
            createResult: created
        )
        let viewModel = LearningDecksViewModel(service: service)
        await viewModel.load()

        service.pauseNextListResponse()
        let refreshTask = Task { await viewModel.load() }
        let didPause = await waitUntil { service.listResponsePaused }
        XCTAssertTrue(didPause)

        _ = await viewModel.createDeck(contentId: 77)
        service.resumeListResponse()
        await refreshTask.value

        XCTAssertEqual(viewModel.decks.map(\.id), [2, 1])
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

    private var listedDeckResponses: [[LearningDeck]]
    private var fetchResults: [Result<LearningDeck, Error>]
    private let createResult: LearningDeck?
    private var retryResults: [Result<LearningDeck, Error>]
    private var viewerURLs: [URL]
    private let viewerError: Error?

    private(set) var fetchCallCount = 0
    private(set) var retryCallCount = 0
    private(set) var viewerCallCount = 0
    private(set) var createRequests: [CreateRequest] = []
    private let listStateLock = NSLock()
    private var shouldPauseNextList = false
    private var shouldKeepListPaused = false

    var listResponsePaused: Bool {
        listStateLock.withLock { shouldKeepListPaused }
    }

    init(
        listedDecks: [LearningDeck] = [],
        listedDeckResponses: [[LearningDeck]]? = nil,
        fetchResults: [Result<LearningDeck, Error>] = [],
        createResult: LearningDeck? = nil,
        retryResults: [Result<LearningDeck, Error>] = [],
        viewerURLs: [URL] = [],
        viewerError: Error? = nil
    ) {
        self.listedDeckResponses = listedDeckResponses ?? [listedDecks]
        self.fetchResults = fetchResults
        self.createResult = createResult
        self.retryResults = retryResults
        self.viewerURLs = viewerURLs
        self.viewerError = viewerError
    }

    func listDecks() async throws -> LearningDeckListResponse {
        let shouldPause = listStateLock.withLock {
            let result = shouldPauseNextList
            shouldPauseNextList = false
            shouldKeepListPaused = result
            return result
        }
        while shouldPause, listStateLock.withLock({ shouldKeepListPaused }) {
            try await Task.sleep(for: .milliseconds(1))
        }
        let decks = listedDeckResponses.count == 1
            ? listedDeckResponses[0]
            : listedDeckResponses.removeFirst()
        return LearningDeckListResponse(decks: decks)
    }

    func pauseNextListResponse() {
        listStateLock.withLock { shouldPauseNextList = true }
    }

    func resumeListResponse() {
        listStateLock.withLock { shouldKeepListPaused = false }
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

    func retryDeck(deckId: Int) async throws -> LearningDeck {
        retryCallCount += 1
        guard !retryResults.isEmpty else {
            throw LearningDeckTestError.unimplemented
        }
        let result = retryResults.count == 1 ? retryResults[0] : retryResults.removeFirst()
        return try result.get()
    }

    func viewerURL(deckId: Int) async throws -> URL {
        viewerCallCount += 1
        if let viewerError {
            throw viewerError
        }
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
