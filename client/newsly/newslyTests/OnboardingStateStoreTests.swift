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

    func testRestoredOriginalFilterIsEditableAndOptOutSurvivesRestart() async {
        let user = makeUser(id: 61)
        let service = DeferredOnboardingService()
        store.saveProgress(userId: user.id, snapshot: OnboardingProgressSnapshot(
            step: .reddit, isPersonalized: true, suggestions: nil, selectedSuggestionIds: [],
            discoveryRunId: 77, discoveryRunStatus: "completed", discoveryErrorMessage: nil,
            hasReachedPollingLimit: false, topicSummary: "An inferred summary", inferredTopics: []))
        let model = OnboardingViewModel(user: user, service: service,
            dictationService: FakeSpeechTranscriber(), onboardingStateStore: store)
        let resume = Task { await model.resumeDiscoveryIfNeeded() }
        let didRequest = await waitUntil { service.hasPendingDiscoveryStatus }
        XCTAssertTrue(didRequest)
        let exact = "  Only space; never celebrity news.\nKeep these words.  "
        service.resolveDiscoveryStatus(OnboardingDiscoveryStatusResponse(
            runId: 77, runStatus: "completed", topicSummary: "Different summary", inferredTopics: [],
            lanes: [], suggestions: nil, errorMessage: nil, originalInputText: exact,
            originalInputKind: "speech_transcript"))
        await resume.value
        XCTAssertEqual(model.newsFilterText, exact)
        XCTAssertTrue(model.newsFilterEnabled)
        XCTAssertEqual(model.step, .reddit)
        model.updateNewsFilter(text: exact + " edited", enabled: false)
        let restored = OnboardingViewModel(user: user, service: service,
            dictationService: FakeSpeechTranscriber(), onboardingStateStore: store)
        XCTAssertEqual(restored.newsFilterText, exact + " edited")
        XCTAssertFalse(restored.newsFilterEnabled)
        await restored.completeOnboarding()
        XCTAssertEqual(service.completedRequest?.newsFilterSeed?.text, exact + " edited")
        XCTAssertEqual(service.completedRequest?.newsFilterSeed?.enabled, false)
    }

    func testTypedDiscoveryPreservesExactInputAndKind() async {
        let service = DeferredOnboardingService(immediateAudioResponse:
            OnboardingAudioDiscoverResponse(runId: 78, runStatus: "pending", topicSummary: nil,
                inferredTopics: [], lanes: []))
        let model = OnboardingViewModel(user: makeUser(id: 63), service: service,
            dictationService: FakeSpeechTranscriber(), onboardingStateStore: store)
        model.startPersonalized()
        let original = "  Quantum research, but never crypto.\n  "
        await model.discoverTypedInterests(original)
        XCTAssertEqual(service.audioRequest?.transcript, original)
        XCTAssertEqual(service.audioRequest?.inputKind, "typed")
        model.chooseDefaults()
        if service.hasPendingDiscoveryStatus {
            service.resolveDiscoveryStatus(OnboardingDiscoveryStatusResponse(
                runId: 78, runStatus: "completed", topicSummary: nil, inferredTopics: [],
                lanes: [], suggestions: nil, errorMessage: nil))
        }
    }

    func testMultibyteOnboardingDraftCanOptOutButCannotActivate() async {
        let user = makeUser(id: 64)
        let service = DeferredOnboardingService()
        store.saveProgress(userId: user.id, snapshot: OnboardingProgressSnapshot(
            step: .reddit, isPersonalized: true, suggestions: nil, selectedSuggestionIds: [],
            discoveryRunId: 79, discoveryRunStatus: "completed", discoveryErrorMessage: nil,
            hasReachedPollingLimit: false, topicSummary: nil, inferredTopics: []))
        let model = OnboardingViewModel(user: user, service: service,
            dictationService: FakeSpeechTranscriber(), onboardingStateStore: store)
        let text = String(repeating: "界", count: 3_000)
        model.updateNewsFilter(text: text, enabled: true)
        await model.completeOnboarding()
        XCTAssertNil(service.completedRequest)
        XCTAssertNotNil(model.errorMessage)
        model.updateNewsFilter(text: text, enabled: false)
        await model.completeOnboarding()
        XCTAssertEqual(service.completedRequest?.newsFilterSeed?.text, text)
        XCTAssertEqual(service.completedRequest?.newsFilterSeed?.enabled, false)
    }

    func testCompletionSeedWireEncodingOmitsAbsenceAndRejectsExplicitNull() throws {
        let absent = APIOnboardingCompleteRequest()
        let data = try JSONEncoder().encode(absent)
        var body = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertNil(body["news_filter_seed"])
        XCTAssertNoThrow(try JSONDecoder().decode(APIOnboardingCompleteRequest.self, from: data))
        body["news_filter_seed"] = NSNull()
        let nullData = try JSONSerialization.data(withJSONObject: body)
        XCTAssertThrowsError(try JSONDecoder().decode(APIOnboardingCompleteRequest.self, from: nullData))
    }

    func testQuickStartCompletionOmitsFilterSeed() async throws {
        let service = DeferredOnboardingService()
        let model = OnboardingViewModel(user: makeUser(id: 62), service: service,
            dictationService: FakeSpeechTranscriber(), onboardingStateStore: store)
        model.chooseDefaults()
        await model.completeOnboarding()
        XCTAssertNil(service.completedRequest?.newsFilterSeed)
        let request = try XCTUnwrap(service.completedRequest)
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(request.api)) as? [String: Any]
        XCTAssertNil(body?["news_filter_seed"])
    }

    func testChooseDefaultsPersistsSuggestionsStepForResume() {
        let user = makeUser(id: 41)
        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService(client: makeTestAPIClient()),
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
            service: OnboardingService(client: makeTestAPIClient()),
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
            service: OnboardingService(client: makeTestAPIClient()),
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
            service: OnboardingService(client: makeTestAPIClient()),
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
                selectedAggregators: ["brutalist", "arxiv"],
                selectedAggregatorTopics: ["brutalist": ["science"], "arxiv": ["cs.CL", "cs.LG"]],
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
            service: OnboardingService(client: makeTestAPIClient()),
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        XCTAssertEqual(viewModel.step, .reddit)
        XCTAssertEqual(viewModel.selectedSuggestionIDs, [441])
        XCTAssertEqual(viewModel.selectedAggregators, ["brutalist", "arxiv"])
        XCTAssertEqual(
            viewModel.selectedAggregatorTopics,
            ["brutalist": ["science"], "arxiv": ["cs.CL", "cs.LG"]]
        )
    }

    func testAggregatorTopicsRoundTripThroughStore() {
        let user = makeUser(id: 46)
        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService(client: makeTestAPIClient()),
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )
        viewModel.advanceToAggregators()
        viewModel.toggleAggregatorTopic("cs.CV", aggregatorKey: "arxiv")
        viewModel.toggleAggregatorTopic("sports", aggregatorKey: "brutalist")

        let snapshot = store.progress(userId: user.id)
        XCTAssertEqual(snapshot?.selectedAggregatorTopics["arxiv"], ["cs.AI", "cs.CL", "cs.LG", "stat.ML"])
        XCTAssertEqual(snapshot?.selectedAggregatorTopics["brutalist"], ["business", "politics", "science"])
    }

    func testLastAggregatorTopicCannotBeDeselected() {
        let viewModel = OnboardingViewModel(
            user: makeUser(id: 47),
            service: OnboardingService(client: makeTestAPIClient()),
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )
        for topic in ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "stat.ML"] {
            viewModel.toggleAggregatorTopic(topic, aggregatorKey: "arxiv")
        }

        XCTAssertEqual(viewModel.selectedAggregatorTopics["arxiv"], ["stat.ML"])
    }

    func testRestoreDropsTopicsOutsideTheAggregatorCatalog() {
        let user = makeUser(id: 48)
        store.saveProgress(
            userId: user.id,
            snapshot: OnboardingProgressSnapshot(
                step: .aggregators,
                isPersonalized: false,
                suggestions: nil,
                selectedSuggestionIds: [],
                selectedAggregators: ["arxiv"],
                selectedAggregatorTopics: ["arxiv": ["cs.CL", "physics.optics"], "hfpapers": ["cs.CL"]],
                discoveryRunId: nil,
                discoveryRunStatus: nil,
                discoveryErrorMessage: nil,
                hasReachedPollingLimit: false,
                topicSummary: nil,
                inferredTopics: []
            )
        )

        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService(client: makeTestAPIClient()),
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        XCTAssertEqual(viewModel.selectedAggregatorTopics["arxiv"], ["cs.CL"])
        XCTAssertNil(viewModel.selectedAggregatorTopics["hfpapers"])
    }

    func testLegacyBrutalistTopicsDecodeIntoBrutalistEntry() throws {
        let user = makeUser(id: 47)
        let legacyJSON = """
        {"\(user.id)": {
            "step": 6,
            "isPersonalized": true,
            "selectedSuggestionIds": [],
            "selectedAggregators": ["brutalist"],
            "selectedBrutalistTopics": ["science", "sports"],
            "hasReachedPollingLimit": false,
            "inferredTopics": []
        }}
        """
        defaults.set(Data(legacyJSON.utf8), forKey: "onboarding_progress")

        let snapshot = try XCTUnwrap(store.progress(userId: user.id))
        XCTAssertEqual(snapshot.selectedAggregatorTopics, ["brutalist": ["science", "sports"]])

        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService(client: makeTestAPIClient()),
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )
        XCTAssertEqual(viewModel.step, .aggregators)
        XCTAssertEqual(viewModel.selectedAggregators, ["brutalist"])
        XCTAssertEqual(viewModel.selectedAggregatorTopics["brutalist"], ["science", "sports"])
        XCTAssertEqual(
            viewModel.selectedAggregatorTopics["arxiv"],
            ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "stat.ML"]
        )
    }

    func testLegacyFastNewsSnapshotRestoresToAggregatorStep() {
        let user = makeUser(id: 45)
        let legacySnapshot: [String: Any] = [
            "step": 5,
            "isPersonalized": true,
            "selectedSuggestionIds": [],
            "selectedAggregators": ["sciurls"],
            "discoveryRunStatus": "completed",
            "hasReachedPollingLimit": false,
            "inferredTopics": []
        ]
        defaults.set(
            try! JSONSerialization.data(withJSONObject: [String(user.id): legacySnapshot]),
            forKey: "onboarding_progress"
        )

        let viewModel = OnboardingViewModel(
            user: user,
            service: OnboardingService(client: makeTestAPIClient()),
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        XCTAssertEqual(viewModel.step, .aggregators)
        XCTAssertTrue(viewModel.selectedSuggestionIDs.isEmpty)
        XCTAssertEqual(viewModel.selectedAggregators, ["sciurls"])
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

    func testCompletionSendsSelectedTopicsForAggregatorsWithTopicCatalogs() async {
        let service = DeferredOnboardingService()
        let viewModel = OnboardingViewModel(
            user: makeUser(id: 51),
            service: service,
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )
        viewModel.selectedAggregators = ["arxiv", "hfpapers", "brutalist", "hackernews"]
        viewModel.toggleAggregatorTopic("cs.CV", aggregatorKey: "arxiv")
        viewModel.toggleAggregatorTopic("stat.ML", aggregatorKey: "arxiv")

        await viewModel.completeOnboarding()

        XCTAssertEqual(
            service.completedRequest?.selectedAggregators,
            [
                OnboardingSelectedAggregator(key: "hackernews", title: "Hacker News"),
                OnboardingSelectedAggregator(
                    key: "brutalist",
                    title: "Brutalist Report",
                    topics: ["business", "politics", "science", "sports"]
                ),
                OnboardingSelectedAggregator(
                    key: "arxiv",
                    title: "arXiv",
                    topics: ["cs.AI", "cs.CL", "cs.LG"]
                ),
                OnboardingSelectedAggregator(key: "hfpapers", title: "Hugging Face Papers"),
            ]
        )
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
    private(set) var audioRequest: OnboardingAudioDiscoverRequest?

    init(immediateAudioResponse: OnboardingAudioDiscoverResponse? = nil) {
        self.immediateAudioResponse = immediateAudioResponse
    }

    var hasPendingAudioDiscovery: Bool { audioContinuation != nil }
    var hasPendingDiscoveryStatus: Bool { statusContinuation != nil }

    func audioDiscover(
        request: OnboardingAudioDiscoverRequest
    ) async throws -> OnboardingAudioDiscoverResponse {
        audioRequest = request
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
