//
//  OnboardingViewModel.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import Foundation
import Observation
import os.log

private let onboardingDiscoveryPollingTimeoutSeconds: TimeInterval = 120
private let onboardingDiscoveryPollingIntervalNanoseconds: UInt64 = 500_000_000
private let onboardingVoiceLogger = Logger(subsystem: "com.newsly", category: "OnboardingVoice")

private func onboardingVoiceElapsedMilliseconds(since start: Date) -> Int {
    Int(Date().timeIntervalSince(start) * 1000)
}

@MainActor
protocol OnboardingServicing: AnyObject {
    func audioDiscover(
        request: OnboardingAudioDiscoverRequest
    ) async throws -> OnboardingAudioDiscoverResponse
    func discoveryStatus(runId: Int) async throws -> OnboardingDiscoveryStatusResponse
    func complete(request: OnboardingCompleteRequest) async throws -> OnboardingCompleteResponse
}

extension OnboardingService: OnboardingServicing {}

enum OnboardingStep: Int, Codable {
    case intro = 0
    case choice = 1
    case audio = 2
    case loading = 3
    case suggestions = 4
    case fastNews = 5
    case aggregators = 6
    case reddit = 7
}

struct OnboardingAggregatorOption: Hashable, Identifiable {
    let key: String
    let title: String
    let subtitle: String
    let icon: String

    var id: String { key }
}

let onboardingAggregatorOptions: [OnboardingAggregatorOption] = [
    OnboardingAggregatorOption(
        key: "hackernews",
        title: "Hacker News",
        subtitle: "Tech, startups, engineering discussion",
        icon: "terminal"
    ),
    OnboardingAggregatorOption(
        key: "techmeme",
        title: "Techmeme",
        subtitle: "Top tech industry headlines, clustered",
        icon: "newspaper"
    ),
    OnboardingAggregatorOption(
        key: "mediagazer",
        title: "Mediagazer",
        subtitle: "Media industry news and coverage",
        icon: "tv"
    ),
    OnboardingAggregatorOption(
        key: "memeorandum",
        title: "Memeorandum",
        subtitle: "US politics, policy, punditry",
        icon: "building.columns"
    ),
    OnboardingAggregatorOption(
        key: "sciurls",
        title: "SciURLs",
        subtitle: "Science news across disciplines",
        icon: "atom"
    ),
    OnboardingAggregatorOption(
        key: "finurls",
        title: "FinURLs",
        subtitle: "Finance, markets, and economics",
        icon: "chart.line.uptrend.xyaxis"
    ),
    OnboardingAggregatorOption(
        key: "brutalist",
        title: "Brutalist Report",
        subtitle: "Headlines by topic — pick your beats",
        icon: "rectangle.grid.2x2"
    ),
]

let onboardingBrutalistTopics: [String] = [
    "science", "business", "politics", "sports",
]

enum OnboardingAudioState: Equatable {
    case idle
    case starting
    case recording
    case transcribing
    case failed

    var accessibilityIdentifier: String {
        switch self {
        case .idle: "idle"
        case .starting: "starting"
        case .recording: "recording"
        case .transcribing: "transcribing"
        case .failed: "failed"
        }
    }
}

@MainActor
@Observable
final class OnboardingViewModel {
    private static let voiceRecordingDeadlines = SpeechRecordingDeadlines(
        noSpeechTimeoutSeconds: 10,
        maximumDurationSeconds: 30
    )

    private enum TaskKey: Hashable {
        case discoveryPolling
    }

    var step: OnboardingStep = .intro
    var suggestions: OnboardingFastDiscoverResponse?
    var selectedSourceKeys: Set<String> = []
    var selectedSubreddits: Set<String> = []
    var selectedAggregators: Set<String> = []
    var selectedBrutalistTopics: Set<String> = Set(onboardingBrutalistTopics)
    var isLoading = false
    var loadingMessage = ""
    var errorMessage: String?
    var completionResponse: OnboardingCompleteResponse?
    var isPersonalized = false

    var audioState: OnboardingAudioState = .idle
    var audioDurationSeconds: Int = 0
    var hasMicPermissionDenied = false
    var hasDictationError = false

    var discoveryLanes: [OnboardingDiscoveryLaneStatus] = []
    var discoveryRunId: Int?
    var discoveryRunStatus: String?
    var discoveryErrorMessage: String?
    var hasReachedDiscoveryPollingLimit = false
    var topicSummary: String?
    var inferredTopics: [String] = []
    var twitterUsername: String = ""

    @ObservationIgnored
    private let service: any OnboardingServicing
    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let onboardingStateStore: OnboardingStateStore
    @ObservationIgnored
    private let user: User
    @ObservationIgnored
    private var audioTimer: Timer?
    @ObservationIgnored
    private let tasks = TaskBag<TaskKey>()
    @ObservationIgnored
    private var didAutoStartRecording = false
    @ObservationIgnored
    private var didAttemptResume = false
    @ObservationIgnored
    private var isSubmittingAudioDiscovery = false
    @ObservationIgnored
    private var audioCaptureStartedAt: Date?
    @ObservationIgnored
    private var discoveryGeneration = 0

    init(
        user: User,
        service: any OnboardingServicing,
        dictationService: (any SpeechTranscribing)? = nil,
        onboardingStateStore: OnboardingStateStore
    ) {
        self.user = user
        self.service = service
        let resolvedDictationService = dictationService ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.voiceCoordinator = VoiceDictationCoordinator(
            transcriber: resolvedDictationService,
            deadlines: Self.voiceRecordingDeadlines
        )
        self.onboardingStateStore = onboardingStateStore
        self.twitterUsername = user.twitterUsername ?? ""

        if !user.hasCompletedOnboarding,
           let snapshot = onboardingStateStore.progress(userId: user.id)
        {
            restoreProgress(snapshot)
        }
    }

    deinit {
        tasks.cancelAll()
        audioTimer?.invalidate()
        MainActor.assumeIsolated {
            voiceCoordinator.cancel()
        }
    }

    var substackSuggestions: [OnboardingSuggestion] {
        suggestions?.recommendedSubstacks ?? []
    }

    var podcastSuggestions: [OnboardingSuggestion] {
        suggestions?.recommendedPods ?? []
    }

    var subredditSuggestions: [OnboardingSuggestion] {
        suggestions?.recommendedSubreddits ?? []
    }

    var isShowingDefaultConfirmation: Bool {
        step == .suggestions && !isPersonalized && suggestionsAreEmpty
    }

    var shouldOfferRetryFromSuggestions: Bool {
        step == .suggestions && isPersonalized && suggestionsAreEmpty
    }

    var shouldOfferRetryFromLoading: Bool {
        step == .loading && (hasReachedDiscoveryPollingLimit || isDiscoveryFailedStatus(discoveryRunStatus))
    }

    var shouldOfferContinueWaiting: Bool {
        step == .loading
            && hasReachedDiscoveryPollingLimit
            && discoveryRunId != nil
            && !isDiscoveryTerminalStatus(discoveryRunStatus)
    }

    func advanceToChoice() {
        step = .choice
    }

    func chooseDefaults() {
        isPersonalized = false
        stopAudioCapture()
        clearDiscoveryState()
        errorMessage = nil
        step = .suggestions
        persistProgress()
    }

    func startPersonalized() {
        clearDiscoveryState()
        isPersonalized = true
        step = .audio
        resetAudioState()
    }

    func retryPersonalization() {
        startPersonalized()
    }

    func continueWaitingForDiscovery() {
        guard let runId = discoveryRunId else { return }
        let generation = discoveryGeneration
        hasReachedDiscoveryPollingLimit = false
        discoveryErrorMessage = nil
        persistProgress()
        startPolling(runId: runId, generation: generation)
    }

    func resumeDiscoveryIfNeeded() async {
        guard !didAttemptResume else { return }
        didAttemptResume = true

        guard step == .loading else { return }
        guard let runId = discoveryRunId ?? onboardingStateStore.discoveryRunId(userId: user.id) else {
            return
        }

        discoveryRunId = runId
        let generation = discoveryGeneration
        await refreshDiscoveryStatus(runId: runId, generation: generation)

        guard generation == discoveryGeneration, discoveryRunId == runId else { return }
        if isDiscoveryTerminalStatus(discoveryRunStatus) || hasReachedDiscoveryPollingLimit {
            return
        }
        guard step == .loading else { return }
        startPolling(runId: runId, generation: generation)
    }

    func startAudioCaptureIfNeeded() async {
        guard !didAutoStartRecording else { return }
        didAutoStartRecording = true
        await startAudioCapture()
    }

    func startAudioCapture() async {
        guard audioState == .idle || audioState == .failed else { return }
        let startedAt = Date()
        errorMessage = nil
        hasMicPermissionDenied = false
        hasDictationError = false
        audioState = .starting
        audioCaptureStartedAt = startedAt
        onboardingVoiceLogger.info("Onboarding audio capture start requested")

        do {
            try await voiceCoordinator.start(
                onTranscriptFinal: { [weak self] transcript in
                    await self?.handleFinalTranscript(transcript)
                },
                onError: { [weak self] message in
                    self?.handleAudioErrorMessage(message)
                },
                onStateChange: { [weak self] state in
                    self?.applyDictationState(state)
                },
                onStopReason: { [weak self] reason in
                    self?.handleDictationStopReason(reason)
                }
            )
            onboardingVoiceLogger.info(
                "Onboarding audio capture started | elapsedMs=\(onboardingVoiceElapsedMilliseconds(since: startedAt))"
            )
        } catch where ClientFailure.classify(error) == .cancelled {
            onboardingVoiceLogger.debug(
                "Onboarding audio capture start cancelled | elapsedMs=\(onboardingVoiceElapsedMilliseconds(since: startedAt))"
            )
        } catch {
            guard step == .audio else { return }
            onboardingVoiceLogger.error(
                "Onboarding audio capture failed | elapsedMs=\(onboardingVoiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            handleAudioError(error)
        }
    }

    func stopAudioCaptureAndDiscover() async {
        guard audioState == .recording else { return }
        let startedAt = Date()
        audioState = .transcribing
        stopAudioTimer()
        onboardingVoiceLogger.info(
            "Onboarding audio capture stop requested | captureElapsedMs=\(self.audioCaptureStartedAt.map { onboardingVoiceElapsedMilliseconds(since: $0) } ?? 0)"
        )
        do {
            let transcript = try await voiceCoordinator.stop()
            await handleFinalTranscript(transcript)
            onboardingVoiceLogger.info(
                "Onboarding audio capture stopped | elapsedMs=\(onboardingVoiceElapsedMilliseconds(since: startedAt))"
            )
        } catch {
            onboardingVoiceLogger.error(
                "Onboarding audio capture stop failed | elapsedMs=\(onboardingVoiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            handleAudioError(error)
        }
    }

    func resetAudioState() {
        voiceCoordinator.cancel()
        audioState = .idle
        audioDurationSeconds = 0
        hasMicPermissionDenied = false
        hasDictationError = false
        errorMessage = nil
        didAutoStartRecording = false
        stopAudioTimer()
    }

    func toggleSource(_ suggestion: OnboardingSuggestion) {
        guard let feedURL = suggestion.feedURL, !feedURL.isEmpty else { return }
        if selectedSourceKeys.contains(feedURL) {
            selectedSourceKeys.remove(feedURL)
        } else {
            selectedSourceKeys.insert(feedURL)
        }
        persistProgress()
    }

    func toggleSubreddit(_ suggestion: OnboardingSuggestion) {
        guard let subreddit = suggestion.subreddit, !subreddit.isEmpty else { return }
        if selectedSubreddits.contains(subreddit) {
            selectedSubreddits.remove(subreddit)
        } else {
            selectedSubreddits.insert(subreddit)
        }
        persistProgress()
    }

    func toggleAggregator(_ option: OnboardingAggregatorOption) {
        if selectedAggregators.contains(option.key) {
            selectedAggregators.remove(option.key)
        } else {
            selectedAggregators.insert(option.key)
        }
        persistProgress()
    }

    func toggleBrutalistTopic(_ topic: String) {
        if selectedBrutalistTopics.contains(topic) {
            selectedBrutalistTopics.remove(topic)
        } else {
            selectedBrutalistTopics.insert(topic)
        }
        persistProgress()
    }

    func advanceToAggregators() {
        errorMessage = nil
        step = .aggregators
        persistProgress()
    }

    func advanceToReddit() {
        errorMessage = nil
        step = .reddit
        persistProgress()
    }

    func returnToAggregators() {
        errorMessage = nil
        step = .aggregators
        persistProgress()
    }

    func returnToSuggestions() {
        errorMessage = nil
        step = .suggestions
        persistProgress()
    }

    func completeOnboarding() async {
        errorMessage = nil
        isLoading = true
        loadingMessage = "Setting up your inbox"
        defer { isLoading = false }

        do {
            let request = OnboardingCompleteRequest(
                selectedSources: buildSelectedSources(),
                selectedSubreddits: Array(selectedSubreddits),
                selectedAggregators: buildSelectedAggregators(),
                profileSummary: isPersonalized ? topicSummary : nil,
                inferredTopics: isPersonalized ? inferredTopics : nil,
                twitterUsername: normalizedTwitterUsername()
            )
            let response = try await service.complete(request: request)
            completionResponse = response
            onboardingStateStore.clearProgress(userId: user.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func buildSelectedAggregators() -> [OnboardingSelectedAggregator] {
        onboardingAggregatorOptions.compactMap { option in
            guard selectedAggregators.contains(option.key) else { return nil }
            let topics: [String] = option.key == "brutalist"
                ? Array(selectedBrutalistTopics).sorted()
                : []
            return OnboardingSelectedAggregator(
                key: option.key,
                title: option.title,
                topics: topics
            )
        }
    }

    private func beginDiscovery(transcript: String) async {
        guard !isSubmittingAudioDiscovery else { return }
        discoveryGeneration += 1
        let generation = discoveryGeneration
        let startedAt = Date()
        isSubmittingAudioDiscovery = true
        defer {
            if generation == discoveryGeneration {
                isSubmittingAudioDiscovery = false
                audioCaptureStartedAt = nil
            }
        }

        do {
            onboardingVoiceLogger.info(
                "Onboarding audio discovery begin | transcriptChars=\(transcript.count)"
            )
            let request = OnboardingAudioDiscoverRequest(
                transcript: transcript,
                locale: Locale.current.identifier
            )
            let response = try await service.audioDiscover(request: request)
            guard generation == discoveryGeneration, step == .audio else { return }
            discoveryRunId = response.runId
            discoveryRunStatus = response.runStatus
            topicSummary = response.topicSummary
            inferredTopics = response.inferredTopics
            discoveryLanes = response.lanes
            hasReachedDiscoveryPollingLimit = false
            step = .loading
            persistProgress()
            startPolling(runId: response.runId, generation: generation)
            onboardingVoiceLogger.info(
                "Onboarding audio discovery started | runId=\(response.runId) laneCount=\(response.lanes.count) elapsedMs=\(onboardingVoiceElapsedMilliseconds(since: startedAt))"
            )
        } catch {
            onboardingVoiceLogger.error(
                "Onboarding audio discovery failed | elapsedMs=\(onboardingVoiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            guard generation == discoveryGeneration, step == .audio else { return }
            errorMessage = error.localizedDescription
            audioState = .failed
            hasDictationError = true
        }
    }

    private func refreshDiscoveryStatus(runId: Int, generation: Int) async {
        do {
            let status = try await service.discoveryStatus(runId: runId)
            guard generation == discoveryGeneration,
                  discoveryRunId == runId,
                  status.runId == runId,
                  step == .loading
            else { return }
            applyDiscoveryStatus(status)
        } catch {
            guard generation == discoveryGeneration,
                  discoveryRunId == runId,
                  step == .loading
            else { return }
            discoveryErrorMessage = error.localizedDescription
            persistProgress()
        }
    }

    private func startPolling(runId: Int, generation: Int) {
        tasks.runReplacing(.discoveryPolling) { [weak self] token in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(onboardingDiscoveryPollingTimeoutSeconds)
            while !Task.isCancelled {
                guard self.tasks.isCurrent(token),
                      generation == self.discoveryGeneration,
                      self.discoveryRunId == runId,
                      self.step == .loading
                else { return }

                await self.refreshDiscoveryStatus(runId: runId, generation: generation)

                guard self.tasks.isCurrent(token),
                      generation == self.discoveryGeneration,
                      self.discoveryRunId == runId
                else { return }

                if self.isDiscoveryTerminalStatus(self.discoveryRunStatus) {
                    break
                }

                guard self.step == .loading else { return }

                if Date() >= deadline {
                    self.handleDiscoveryTimeout()
                    break
                }

                do {
                    try await Task.sleep(
                        nanoseconds: onboardingDiscoveryPollingIntervalNanoseconds
                    )
                } catch {
                    return
                }
            }
        }
    }

    private func applyDiscoveryStatus(_ status: OnboardingDiscoveryStatusResponse) {
        discoveryRunId = status.runId
        discoveryRunStatus = status.runStatus
        discoveryLanes = status.lanes
        topicSummary = status.topicSummary
        inferredTopics = status.inferredTopics
        discoveryErrorMessage = status.errorMessage
        hasReachedDiscoveryPollingLimit = false

        if discoveryTaskStatus(status.runStatus) == .completed {
            if let suggestions = status.suggestions {
                applySuggestions(suggestions)
            } else {
                suggestions = nil
                selectedSourceKeys = []
                selectedSubreddits = []
            }
            errorMessage = nil
            step = .suggestions
            persistProgress()
            return
        }

        if discoveryTaskStatus(status.runStatus) == .failed {
            suggestions = nil
            selectedSourceKeys = []
            selectedSubreddits = []
            errorMessage = nil
            step = .loading
            persistProgress()
            return
        }

        persistProgress()
    }

    private func applySuggestions(_ response: OnboardingFastDiscoverResponse) {
        suggestions = response
        let sourceKeys = (response.recommendedSubstacks + response.recommendedPods)
            .compactMap { $0.feedURL }
        selectedSourceKeys = Set(sourceKeys)
        let subredditKeys = response.recommendedSubreddits.compactMap { $0.subreddit }
        selectedSubreddits = Set(subredditKeys)
    }

    private func buildSelectedSources() -> [OnboardingSelectedSource] {
        let combined = substackSuggestions + podcastSuggestions
        return combined.compactMap { suggestion in
            guard let feedURL = suggestion.feedURL, selectedSourceKeys.contains(feedURL) else { return nil }
            return OnboardingSelectedSource(
                suggestionType: suggestion.suggestionType,
                title: suggestion.title,
                feedURL: feedURL,
                config: nil
            )
        }
    }

    private func normalizedTwitterUsername() -> String? {
        let trimmed = twitterUsername.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return trimmed.hasPrefix("@") ? String(trimmed.dropFirst()) : trimmed
    }

    private func handleAudioError(_ error: Error) {
        errorMessage = error.localizedDescription
        if let dictationError = error as? VoiceDictationError {
            switch dictationError {
            case .noMicrophoneAccess:
                hasMicPermissionDenied = true
                audioState = .failed
            default:
                hasDictationError = true
                audioState = .failed
            }
        } else {
            hasDictationError = true
            audioState = .failed
        }
        stopAudioTimer()
    }

    private func handleDiscoveryTimeout() {
        hasReachedDiscoveryPollingLimit = true
        discoveryErrorMessage = "Still searching — usually wraps up in a moment."
        errorMessage = nil
        persistProgress()
    }

    private func clearDiscoveryState() {
        discoveryGeneration += 1
        tasks.cancel(.discoveryPolling)
        discoveryRunId = nil
        discoveryRunStatus = nil
        discoveryLanes = []
        discoveryErrorMessage = nil
        hasReachedDiscoveryPollingLimit = false
        topicSummary = nil
        inferredTopics = []
        suggestions = nil
        selectedSourceKeys = []
        selectedSubreddits = []
        selectedAggregators = []
        selectedBrutalistTopics = Set(onboardingBrutalistTopics)
        isSubmittingAudioDiscovery = false
        onboardingStateStore.clearProgress(userId: user.id)
    }

    private func stopAudioCapture() {
        voiceCoordinator.cancel()
        stopAudioTimer()
        audioState = .idle
        if let audioCaptureStartedAt {
            onboardingVoiceLogger.info(
                "Onboarding audio capture cancelled | captureElapsedMs=\(onboardingVoiceElapsedMilliseconds(since: audioCaptureStartedAt))"
            )
        }
        audioCaptureStartedAt = nil
    }

    private func handleFinalTranscript(_ transcript: String) async {
        guard step == .audio else { return }
        let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            handleAudioErrorMessage("No speech detected. Please try again.")
            return
        }

        audioState = .transcribing
        stopAudioTimer()
        onboardingVoiceLogger.info(
            "Onboarding transcript final | transcriptChars=\(trimmed.count) captureElapsedMs=\(self.audioCaptureStartedAt.map { onboardingVoiceElapsedMilliseconds(since: $0) } ?? 0)"
        )
        await beginDiscovery(transcript: trimmed)
    }

    private func handleAudioErrorMessage(_ message: String) {
        guard step == .audio else { return }
        errorMessage = message
        hasDictationError = true
        audioState = .failed
        stopAudioTimer()
    }

    private func applyDictationState(_ state: SpeechTranscriptionState) {
        guard step == .audio else { return }
        switch state {
        case .idle:
            if audioState != .failed {
                audioState = .idle
            }
            stopAudioTimer()
        case .starting:
            audioState = .starting
        case .recording:
            audioState = .recording
            startAudioTimer()
        case .transcribing:
            audioState = .transcribing
            stopAudioTimer()
        case .failed(let message):
            handleAudioErrorMessage(message)
        }
    }

    private func handleDictationStopReason(_ reason: SpeechStopReason) {
        guard step == .audio else { return }
        switch reason {
        case .manual:
            return
        case .silenceAutoStop, .maximumDuration:
            return
        case .noSpeechTimeout:
            handleAudioErrorMessage("No speech detected. Please try again.")
        case .cancel:
            audioState = .idle
            stopAudioTimer()
        case .failure:
            hasDictationError = true
            audioState = .failed
            stopAudioTimer()
        }
    }

    private func startAudioTimer() {
        audioTimer?.invalidate()
        audioDurationSeconds = 0
        audioTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.handleAudioTimerTick()
            }
        }
    }

    private func handleAudioTimerTick() {
        audioDurationSeconds += 1
    }

    private func stopAudioTimer() {
        audioTimer?.invalidate()
        audioTimer = nil
    }

    private var suggestionsAreEmpty: Bool {
        substackSuggestions.isEmpty && podcastSuggestions.isEmpty && subredditSuggestions.isEmpty
    }

    private func discoveryTaskStatus(_ status: String?) -> APITaskStatus? {
        guard let status else { return nil }
        return APITaskStatus(rawValue: status)
    }

    private func isDiscoveryTerminalStatus(_ status: String?) -> Bool {
        let taskStatus = discoveryTaskStatus(status)
        return taskStatus == .completed || taskStatus == .failed
    }

    private func isDiscoveryFailedStatus(_ status: String?) -> Bool {
        discoveryTaskStatus(status) == .failed
    }

    private func restoreProgress(_ snapshot: OnboardingProgressSnapshot) {
        step = snapshot.step == .fastNews ? .aggregators : snapshot.step
        isPersonalized = snapshot.isPersonalized
        suggestions = snapshot.suggestions
        selectedSourceKeys = Set(snapshot.selectedSourceKeys)
        selectedSubreddits = Set(snapshot.selectedSubreddits)
        selectedAggregators = Set(snapshot.selectedAggregators)
        if !snapshot.selectedBrutalistTopics.isEmpty {
            selectedBrutalistTopics = Set(snapshot.selectedBrutalistTopics)
        }
        discoveryRunId = snapshot.discoveryRunId
        discoveryRunStatus = snapshot.discoveryRunStatus
        discoveryErrorMessage = snapshot.discoveryErrorMessage
        hasReachedDiscoveryPollingLimit = snapshot.hasReachedPollingLimit
        topicSummary = snapshot.topicSummary
        inferredTopics = snapshot.inferredTopics
    }

    private func persistProgress() {
        guard !user.hasCompletedOnboarding else {
            onboardingStateStore.clearProgress(userId: user.id)
            return
        }

        guard step == .loading
            || step == .suggestions
            || step == .fastNews
            || step == .aggregators
            || step == .reddit
        else {
            onboardingStateStore.clearProgress(userId: user.id)
            return
        }

        onboardingStateStore.saveProgress(
            userId: user.id,
            snapshot: OnboardingProgressSnapshot(
                step: step,
                isPersonalized: isPersonalized,
                suggestions: suggestions,
                selectedSourceKeys: Array(selectedSourceKeys).sorted(),
                selectedSubreddits: Array(selectedSubreddits).sorted(),
                selectedAggregators: Array(selectedAggregators).sorted(),
                selectedBrutalistTopics: Array(selectedBrutalistTopics).sorted(),
                discoveryRunId: discoveryRunId,
                discoveryRunStatus: discoveryRunStatus,
                discoveryErrorMessage: discoveryErrorMessage,
                hasReachedPollingLimit: hasReachedDiscoveryPollingLimit,
                topicSummary: topicSummary,
                inferredTopics: inferredTopics
            )
        )
    }
}
