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

    func testInitRestoresPersistedSuggestionsStepAndSelections() {
        let user = makeUser(id: 42)
        let response = OnboardingFastDiscoverResponse(
            recommendedPods: [
                makeSuggestion(
                    suggestionType: "podcast_rss",
                    title: "Hard Fork",
                    feedURL: "https://example.com/hard-fork.xml"
                )
            ],
            recommendedSubstacks: [
                makeSuggestion(
                    suggestionType: "substack",
                    title: "Stratechery",
                    feedURL: "https://example.com/stratechery.xml"
                )
            ],
            recommendedSubreddits: [
                makeSuggestion(
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
                selectedSourceKeys: ["https://example.com/hard-fork.xml"],
                selectedSubreddits: ["MachineLearning"],
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
            dictationService: FakeSpeechTranscriber(),
            onboardingStateStore: store
        )

        XCTAssertEqual(viewModel.step, .suggestions)
        XCTAssertTrue(viewModel.isPersonalized)
        XCTAssertEqual(viewModel.substackSuggestions.map(\.displayTitle), ["Stratechery"])
        XCTAssertEqual(viewModel.podcastSuggestions.map(\.displayTitle), ["Hard Fork"])
        XCTAssertEqual(viewModel.selectedSourceKeys, ["https://example.com/hard-fork.xml"])
        XCTAssertEqual(viewModel.selectedSubreddits, ["MachineLearning"])
        XCTAssertEqual(viewModel.topicSummary, "AI and startups")
        XCTAssertEqual(viewModel.inferredTopics, ["AI", "startups"])
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

    private func makeSuggestion(
        suggestionType: String,
        title: String,
        feedURL: String? = nil,
        subreddit: String? = nil
    ) -> OnboardingSuggestion {
        OnboardingSuggestion(
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
}

@MainActor
private final class FakeSpeechTranscriber: SpeechTranscribing {
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptFinal: ((String) -> Void)?
    var onError: ((String) -> Void)?
    var onStateChange: ((SpeechTranscriptionState) -> Void)?
    var onStopReason: ((SpeechStopReason) -> Void)?

    var isAvailable: Bool { true }
    private(set) var isRecording = false
    private(set) var isTranscribing = false

    func start() async throws {
        isRecording = true
    }

    func stop() async throws -> String {
        isRecording = false
        isTranscribing = false
        return ""
    }

    func cancel() {
        isRecording = false
        isTranscribing = false
    }

    func reset() {
        isRecording = false
        isTranscribing = false
    }
}
