import XCTest
@testable import newsly

@MainActor
final class OnboardingStateStoreTests: XCTestCase {
    private var defaults: UserDefaults!
    private var store: OnboardingStateStore!
    private var suiteName: String!

    override func setUp() {
        super.setUp()
        suiteName = "OnboardingStateStoreTests.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suiteName) else {
            fatalError("Failed to create isolated user defaults suite")
        }
        self.defaults = defaults
        defaults.removePersistentDomain(forName: suiteName)
        store = OnboardingStateStore(defaults: defaults)
    }

    override func tearDown() {
        if let suiteName {
            defaults.removePersistentDomain(forName: suiteName)
        }
        defaults = nil
        store = nil
        suiteName = nil
        super.tearDown()
    }

    func testChooseDefaultsPersistsSuggestionsStepForResume() {
        let user = makeUser(id: 41)
        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService.shared,
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        viewModel.chooseDefaults()

        let snapshot = store.progress(userId: user.id)
        XCTAssertEqual(snapshot?.step, .suggestions)
        XCTAssertEqual(snapshot?.isPersonalized, false)
        XCTAssertNil(snapshot?.discoveryRunId)
        XCTAssertTrue(viewModel.isShowingDefaultConfirmation)
    }

    func testVoiceCaptureUsesThirtySecondOnboardingDeadline() async {
        let transcriber = FakeSpeechTranscriber()
        let viewModel = OnboardingViewModel(
            user: makeUser(id: 46),
            service: OnboardingService.shared,
            dictationService: transcriber,
            onboardingStateStore: store
        )
        viewModel.startPersonalized()

        await viewModel.startAudioCapture()

        XCTAssertEqual(
            transcriber.requestedDeadlines,
            [
                SpeechRecordingDeadlines(
                    noSpeechTimeoutSeconds: 10,
                    maximumDurationSeconds: 30
                )
            ]
        )
        viewModel.resetAudioState()
    }

    func testChoosingDefaultsDuringAudioStartupDoesNotRestoreAStaleError() async {
        let transcriber = DeferredStartSpeechTranscriber()
        let viewModel = OnboardingViewModel(
            user: makeUser(id: 49),
            service: OnboardingService.shared,
            dictationService: transcriber,
            onboardingStateStore: store
        )
        viewModel.startPersonalized()

        let startTask = Task { @MainActor in
            await viewModel.startAudioCapture()
        }
        let didStart = await waitUntil { transcriber.isStarting }
        XCTAssertTrue(didStart)

        viewModel.chooseDefaults()
        await startTask.value

        XCTAssertEqual(viewModel.step, .suggestions)
        XCTAssertFalse(viewModel.isPersonalized)
        XCTAssertEqual(viewModel.audioState, .idle)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.hasDictationError)
        XCTAssertFalse(transcriber.hasActiveSession)
    }

    func testAbandonedAudioDiscoveryResponseCannotRestorePersonalizedLoading() async {
        let service = DeferredOnboardingService()
        let viewModel = OnboardingViewModel(
            user: makeUser(id: 47),
            service: service,
            dictationService: FakeSpeechTranscriber(transcript: "AI infrastructure"),
            onboardingStateStore: store
        )
        viewModel.startPersonalized()
        await viewModel.startAudioCapture()
        let didStartRecording = await waitUntil { viewModel.audioState == .recording }
        XCTAssertTrue(didStartRecording)

        let discoveryTask = Task { await viewModel.stopAudioCaptureAndDiscover() }
        let didStartDiscovery = await waitUntil { service.hasPendingAudioDiscovery }
        XCTAssertTrue(didStartDiscovery)

        viewModel.chooseDefaults()
        service.resolveAudioDiscovery(
            OnboardingAudioDiscoverResponse(
                runId: 901,
                runStatus: "pending",
                topicSummary: "Stale personalized profile",
                inferredTopics: ["stale"],
                lanes: []
            )
        )
        await discoveryTask.value

        XCTAssertEqual(viewModel.step, .suggestions)
        XCTAssertFalse(viewModel.isPersonalized)
        XCTAssertNil(viewModel.discoveryRunId)
        XCTAssertNil(viewModel.topicSummary)
        XCTAssertNil(store.progress(userId: 47)?.discoveryRunId)
    }

    func testCancelledPollResponseCannotRestoreAbandonedRunAfterRetry() async {
        let service = DeferredOnboardingService(
            immediateAudioResponse: OnboardingAudioDiscoverResponse(
                runId: 902,
                runStatus: "pending",
                topicSummary: "Current profile",
                inferredTopics: ["AI"],
                lanes: []
            )
        )
        let viewModel = OnboardingViewModel(
            user: makeUser(id: 48),
            service: service,
            dictationService: FakeSpeechTranscriber(transcript: "AI infrastructure"),
            onboardingStateStore: store
        )
        viewModel.startPersonalized()
        await viewModel.startAudioCapture()
        let didStartRecording = await waitUntil { viewModel.audioState == .recording }
        XCTAssertTrue(didStartRecording)
        await viewModel.stopAudioCaptureAndDiscover()
        let didStartPolling = await waitUntil { service.hasPendingDiscoveryStatus }
        XCTAssertTrue(didStartPolling)

        viewModel.retryPersonalization()
        service.resolveDiscoveryStatus(
            OnboardingDiscoveryStatusResponse(
                runId: 902,
                runStatus: "completed",
                topicSummary: "Stale profile",
                inferredTopics: ["stale"],
                lanes: [],
                suggestions: OnboardingFastDiscoverResponse(
                    recommendedPods: [],
                    recommendedSubstacks: [
                        makeSuggestion(
                            id: 9021,
                            suggestionType: "substack",
                            title: "Stale Feed",
                            feedURL: "https://stale.example/feed"
                        )
                    ],
                    recommendedSubreddits: []
                ),
                errorMessage: nil
            )
        )
        await settleAsyncWork()

        XCTAssertEqual(viewModel.step, .audio)
        XCTAssertTrue(viewModel.isPersonalized)
        XCTAssertNil(viewModel.discoveryRunId)
        XCTAssertNil(viewModel.suggestions)
        XCTAssertNil(viewModel.topicSummary)
    }

    func testInitRestoresPersistedSuggestionsStepAndSelections() {
        let user = makeUser(id: 42)
        let response = OnboardingFastDiscoverResponse(
            recommendedPods: [
                makeSuggestion(
                    id: 421,
                    suggestionType: "podcast_rss",
                    title: "Hard Fork",
                    feedURL: "https://example.com/hard-fork.xml"
                )
            ],
            recommendedSubstacks: [
                makeSuggestion(
                    id: 422,
                    suggestionType: "substack",
                    title: "Stratechery",
                    feedURL: "https://example.com/stratechery.xml"
                )
            ],
            recommendedSubreddits: [
                makeSuggestion(
                    id: 423,
                    suggestionType: "reddit",
                    title: "MachineLearning",
                    subreddit: "MachineLearning"
                )
            ]
        )
        store.saveProgress(
            userId: user.id,
            snapshot: OnboardingProgressSnapshot(
                step: .suggestions,
                isPersonalized: true,
                suggestions: response,
                selectedSuggestionIds: [421, 423],
                discoveryRunId: nil,
                discoveryRunStatus: "completed",
                discoveryErrorMessage: nil,
                hasReachedPollingLimit: false,
                topicSummary: "AI and startups",
                inferredTopics: ["AI", "startups"]
            )
        )

        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService.shared,
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        XCTAssertEqual(viewModel.step, .suggestions)
        XCTAssertTrue(viewModel.isPersonalized)
        XCTAssertEqual(viewModel.substackSuggestions.map(\.displayTitle), ["Stratechery"])
        XCTAssertEqual(viewModel.podcastSuggestions.map(\.displayTitle), ["Hard Fork"])
        XCTAssertEqual(viewModel.selectedSuggestionIDs, [421, 423])
        XCTAssertEqual(viewModel.topicSummary, "AI and startups")
        XCTAssertEqual(viewModel.inferredTopics, ["AI", "startups"])
    }

    func testInitRestoresSplitFastNewsStepAndSelections() {
        let user = makeUser(id: 44)
        let response = OnboardingFastDiscoverResponse(
            recommendedPods: [],
            recommendedSubstacks: [],
            recommendedSubreddits: [
                makeSuggestion(
                    id: 441,
                    suggestionType: "reddit",
                    title: "MachineLearning",
                    subreddit: "MachineLearning"
                )
            ]
        )
        store.saveProgress(
            userId: user.id,
            snapshot: OnboardingProgressSnapshot(
                step: .reddit,
                isPersonalized: true,
                suggestions: response,
                selectedSuggestionIds: [441],
                selectedAggregators: ["brutalist"],
                selectedBrutalistTopics: ["science"],
                discoveryRunId: 123,
                discoveryRunStatus: "completed",
                discoveryErrorMessage: nil,
                hasReachedPollingLimit: false,
                topicSummary: "AI and startups",
                inferredTopics: ["AI", "startups"]
            )
        )

        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService.shared,
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        XCTAssertEqual(viewModel.step, .reddit)
        XCTAssertEqual(viewModel.selectedSuggestionIDs, [441])
        XCTAssertEqual(viewModel.selectedAggregators, ["brutalist"])
        XCTAssertEqual(viewModel.selectedBrutalistTopics, ["science"])
    }

    func testLegacyFastNewsSnapshotRestoresToAggregatorStep() {
        let user = makeUser(id: 45)
        store.saveProgress(
            userId: user.id,
            snapshot: OnboardingProgressSnapshot(
                step: .fastNews,
                isPersonalized: true,
                suggestions: nil,
                selectedSuggestionIds: [],
                selectedAggregators: ["sciurls"],
                discoveryRunId: nil,
                discoveryRunStatus: "completed",
                discoveryErrorMessage: nil,
                hasReachedPollingLimit: false,
                topicSummary: nil,
                inferredTopics: []
            )
        )

        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService.shared,
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        XCTAssertEqual(viewModel.step, .aggregators)
        XCTAssertTrue(viewModel.selectedSuggestionIDs.isEmpty)
        XCTAssertEqual(viewModel.selectedAggregators, ["sciurls"])
    }

    func testLegacyDiscoveryRunFallsBackToLoadingSnapshot() throws {
        let user = makeUser(id: 43)
        let encodedRuns = try JSONEncoder().encode([String(user.id): 987])
        defaults.set(encodedRuns, forKey: "onboarding_discovery_runs")

        let snapshot = store.progress(userId: user.id)

        XCTAssertEqual(snapshot?.step, .loading)
        XCTAssertEqual(snapshot?.isPersonalized, true)
        XCTAssertEqual(snapshot?.discoveryRunId, 987)
    }

    func testPersonalizedCompletionSendsOnlyRunAndPersistedSuggestionIDs() async {
        let service = DeferredOnboardingService()
        let viewModel = OnboardingViewModel(
            user: makeUser(id: 50),
            service: service,
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )
        viewModel.isPersonalized = true
        viewModel.discoveryRunId = 500
        viewModel.selectedSuggestionIDs = [501, 503]

        await viewModel.completeOnboarding()

        XCTAssertEqual(service.completedRequest?.discoveryRunId, 500)
        XCTAssertEqual(service.completedRequest?.selectedSuggestionIds, [501, 503])
        XCTAssertNil(viewModel.errorMessage)
    }

    private func makeSuggestion(
        id: Int,
        suggestionType: String,
        title: String,
        feedURL: String? = nil,
        subreddit: String? = nil
    ) -> OnboardingSuggestion {
        OnboardingSuggestion(
            id: id,
            suggestionType: suggestionType,
            title: title,
            siteURL: nil,
            feedURL: feedURL,
            subreddit: subreddit,
            rationale: nil,
            score: nil,
            isDefault: false
        )
    }

    private func makeUser(id: Int) -> User {
        let now = Date(timeIntervalSince1970: 1_710_000_000)
        return User(
            id: id,
            appleId: "apple-\(id)",
            email: "user\(id)@example.com",
            fullName: "User \(id)",
            twitterUsername: nil,
            hasXBookmarkSync: false,
            isAdmin: false,
            isActive: true,
            hasCompletedOnboarding: false,
            hasCompletedNewUserTutorial: false,
            createdAt: now,
            updatedAt: now
        )
    }

    private func waitUntil(
        attempts: Int = 100,
        condition: @escaping @MainActor () -> Bool
    ) async -> Bool {
        for _ in 0..<attempts {
            if condition() { return true }
            await Task.yield()
        }
        return condition()
    }

    private func settleAsyncWork() async {
        for _ in 0..<20 {
            await Task.yield()
        }
    }
}

@MainActor
private final class FakeSpeechTranscriber: SpeechTranscribing {
    var isAvailable: Bool { true }
    private(set) var requestedDeadlines: [SpeechRecordingDeadlines] = []
    private let transcript: String

    init(transcript: String = "") {
        self.transcript = transcript
    }

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        requestedDeadlines.append(deadlines)
        let id = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        return SpeechTranscriptionSession(
            id: id,
            events: pair.stream,
            start: { _ in pair.continuation.yield(.stateChange(.recording)) },
            stop: { [transcript] _ in
                pair.continuation.finish()
                return transcript
            },
            cancel: { _ in pair.continuation.finish() }
        )
    }
}

@MainActor
private final class DeferredStartSpeechTranscriber: SpeechTranscribing {
    var isAvailable: Bool { true }
    private(set) var isStarting = false
    private(set) var hasActiveSession = false

    private var activeSessionID: UUID?
    private var startContinuation: CheckedContinuation<Void, Never>?

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        _ = deadlines
        let id = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSessionID = id
        hasActiveSession = true
        return SpeechTranscriptionSession(
            id: id,
            events: pair.stream,
            start: { [weak self] sessionID in
                guard let self, activeSessionID == sessionID else {
                    throw VoiceDictationError.noActiveSession
                }
                isStarting = true
                await withCheckedContinuation { continuation in
                    self.startContinuation = continuation
                }
                guard activeSessionID == sessionID else {
                    throw VoiceDictationError.noActiveSession
                }
            },
            stop: { _ in throw VoiceDictationError.recordingFailed },
            cancel: { [weak self] sessionID in
                guard let self, activeSessionID == sessionID else { return }
                activeSessionID = nil
                hasActiveSession = false
                startContinuation?.resume()
                startContinuation = nil
                pair.continuation.finish()
            }
        )
    }
}

@MainActor
private final class DeferredOnboardingService: OnboardingServicing {
    private let immediateAudioResponse: OnboardingAudioDiscoverResponse?
    private var audioContinuation: CheckedContinuation<OnboardingAudioDiscoverResponse, Error>?
    private var statusContinuation: CheckedContinuation<OnboardingDiscoveryStatusResponse, Error>?
    private(set) var completedRequest: OnboardingCompleteRequest?

    init(immediateAudioResponse: OnboardingAudioDiscoverResponse? = nil) {
        self.immediateAudioResponse = immediateAudioResponse
    }

    var hasPendingAudioDiscovery: Bool { audioContinuation != nil }
    var hasPendingDiscoveryStatus: Bool { statusContinuation != nil }

    func audioDiscover(
        request: OnboardingAudioDiscoverRequest
    ) async throws -> OnboardingAudioDiscoverResponse {
        _ = request
        if let immediateAudioResponse {
            return immediateAudioResponse
        }
        return try await withCheckedThrowingContinuation { continuation in
            audioContinuation = continuation
        }
    }

    func discoveryStatus(runId: Int) async throws -> OnboardingDiscoveryStatusResponse {
        _ = runId
        return try await withCheckedThrowingContinuation { continuation in
            statusContinuation = continuation
        }
    }

    func complete(request: OnboardingCompleteRequest) async throws -> OnboardingCompleteResponse {
        completedRequest = request
        return OnboardingCompleteResponse(
            status: "queued",
            taskId: 1,
            inboxCountEstimate: 100,
            configuredSourceCount: request.selectedSuggestionIds.count,
            longformStatus: "loading",
            hasCompletedOnboarding: true,
            hasCompletedNewUserTutorial: false
        )
    }

    func resolveAudioDiscovery(_ response: OnboardingAudioDiscoverResponse) {
        let continuation = audioContinuation
        audioContinuation = nil
        continuation?.resume(returning: response)
    }

    func resolveDiscoveryStatus(_ response: OnboardingDiscoveryStatusResponse) {
        let continuation = statusContinuation
        statusContinuation = nil
        continuation?.resume(returning: response)
    }
}
