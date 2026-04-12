//
//  OnboardingViewModel.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import Foundation

private let defaultNewsListPreferencePrompt =
    "Curate a high-signal news list across all sources using these principles: "
    + "prefer original reporting over commentary; prioritize concrete developments, technical insight, "
    + "firsthand company or product updates, meaningful data, and strong analysis; reward pieces that "
    + "add context, synthesis, or clear implications; avoid memes, engagement bait, vague reactions, "
    + "spammy vendor copy, repetitive hype, and low-context chatter unless they contain genuinely new information."

private let onboardingDiscoveryPollingTimeoutSeconds: TimeInterval = 120
private let onboardingDiscoveryPollingIntervalNanoseconds: UInt64 = 500_000_000

enum OnboardingStep: Int, Codable {
    case intro
    case choice
    case audio
    case loading
    case suggestions
}

enum OnboardingAudioState: Equatable {
    case idle
    case recording
    case transcribing
    case error
}

@MainActor
final class OnboardingViewModel: ObservableObject {
    @Published var step: OnboardingStep = .choice
    @Published var suggestions: OnboardingFastDiscoverResponse?
    @Published var selectedSourceKeys: Set<String> = []
    @Published var selectedSubreddits: Set<String> = []
    @Published var isLoading = false
    @Published var loadingMessage = ""
    @Published var errorMessage: String?
    @Published var completionResponse: OnboardingCompleteResponse?
    @Published var isPersonalized = false

    @Published var audioState: OnboardingAudioState = .idle
    @Published var audioDurationSeconds: Int = 0
    @Published var hasMicPermissionDenied = false
    @Published var hasDictationError = false

    @Published var discoveryLanes: [OnboardingDiscoveryLaneStatus] = []
    @Published var discoveryRunId: Int?
    @Published var discoveryRunStatus: String?
    @Published var discoveryErrorMessage: String?
    @Published var hasReachedDiscoveryPollingLimit = false
    @Published var topicSummary: String?
    @Published var inferredTopics: [String] = []
    @Published var twitterUsername: String = ""
    @Published var newsListPreferencePrompt: String = ""

    private let service: OnboardingService
    private let dictationService: any SpeechTranscribing
    private let onboardingStateStore: OnboardingStateStore
    private let user: User
    private var audioTimer: Timer?
    private var pollingTask: Task<Void, Never>?
    private var didAutoStartRecording = false
    private var didAttemptResume = false
    private var isSubmittingAudioDiscovery = false

    init(
        user: User,
        service: OnboardingService = .shared,
        dictationService: (any SpeechTranscribing)? = nil,
        onboardingStateStore: OnboardingStateStore = .shared
    ) {
        self.user = user
        self.service = service
        self.dictationService = dictationService ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.onboardingStateStore = onboardingStateStore
        self.twitterUsername = user.twitterUsername ?? ""
        let trimmedPrompt = user.newsListPreferencePrompt.trimmingCharacters(
            in: .whitespacesAndNewlines
        )
        self.newsListPreferencePrompt =
            trimmedPrompt.isEmpty ? defaultNewsListPreferencePrompt : user.newsListPreferencePrompt

        if !user.hasCompletedOnboarding,
           let snapshot = onboardingStateStore.progress(userId: user.id)
        {
            restoreProgress(snapshot)
        }
    }

    deinit {
        pollingTask?.cancel()
        audioTimer?.invalidate()
        let service = dictationService
        Task { @MainActor in service.cancel() }
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
        step == .loading && (hasReachedDiscoveryPollingLimit || discoveryRunStatus == "failed")
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
        hasReachedDiscoveryPollingLimit = false
        discoveryErrorMessage = nil
        persistProgress()
        startPolling(runId: runId)
    }

    func resumeDiscoveryIfNeeded() async {
        guard !didAttemptResume else { return }
        didAttemptResume = true

        guard step == .loading else { return }
        guard let runId = discoveryRunId ?? onboardingStateStore.discoveryRunId(userId: user.id) else {
            return
        }

        discoveryRunId = runId
        await refreshDiscoveryStatus(runId: runId)

        if isDiscoveryTerminalStatus(discoveryRunStatus) || hasReachedDiscoveryPollingLimit {
            return
        }
        startPolling(runId: runId)
    }

    func startAudioCaptureIfNeeded() async {
        guard !didAutoStartRecording else { return }
        didAutoStartRecording = true
        await startAudioCapture()
    }

    func startAudioCapture() async {
        configureDictationCallbacks()
        errorMessage = nil
        hasMicPermissionDenied = false
        hasDictationError = false
        audioState = .recording
        startAudioTimer()

        do {
            try await dictationService.start()
        } catch {
            handleAudioError(error)
        }
    }

    func stopAudioCaptureAndDiscover() async {
        audioState = .transcribing
        stopAudioTimer()
        do {
            _ = try await dictationService.stop()
        } catch {
            handleAudioError(error)
        }
    }

    func resetAudioState() {
        dictationService.cancel()
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

    func completeOnboarding() async {
        errorMessage = nil
        isLoading = true
        loadingMessage = "Setting up your inbox"
        defer { isLoading = false }

        do {
            let request = OnboardingCompleteRequest(
                selectedSources: buildSelectedSources(),
                selectedSubreddits: Array(selectedSubreddits),
                profileSummary: isPersonalized ? topicSummary : nil,
                inferredTopics: isPersonalized ? inferredTopics : nil,
                twitterUsername: normalizedTwitterUsername(),
                newsListPreferencePrompt: normalizedNewsListPreferencePrompt()
            )
            let response = try await service.complete(request: request)
            completionResponse = response
            onboardingStateStore.clearProgress(userId: user.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func beginDiscovery(transcript: String) async {
        guard !isSubmittingAudioDiscovery else { return }
        isSubmittingAudioDiscovery = true
        defer { isSubmittingAudioDiscovery = false }

        do {
            let request = OnboardingAudioDiscoverRequest(
                transcript: transcript,
                locale: Locale.current.identifier
            )
            let response = try await service.audioDiscover(request: request)
            discoveryRunId = response.runId
            discoveryRunStatus = response.runStatus
            topicSummary = response.topicSummary
            inferredTopics = response.inferredTopics
            discoveryLanes = response.lanes
            hasReachedDiscoveryPollingLimit = false
            step = .loading
            persistProgress()
            startPolling(runId: response.runId)
        } catch {
            errorMessage = error.localizedDescription
            audioState = .error
            hasDictationError = true
        }
    }

    private func refreshDiscoveryStatus(runId: Int) async {
        do {
            let status = try await service.discoveryStatus(runId: runId)
            applyDiscoveryStatus(status)
        } catch {
            discoveryErrorMessage = error.localizedDescription
            persistProgress()
        }
    }

    private func startPolling(runId: Int) {
        pollingTask?.cancel()
        pollingTask = Task { @MainActor in
            let deadline = Date().addingTimeInterval(onboardingDiscoveryPollingTimeoutSeconds)
            while !Task.isCancelled {
                await refreshDiscoveryStatus(runId: runId)

                if isDiscoveryTerminalStatus(discoveryRunStatus) {
                    break
                }

                if Date() >= deadline {
                    handleDiscoveryTimeout()
                    break
                }

                try? await Task.sleep(nanoseconds: onboardingDiscoveryPollingIntervalNanoseconds)
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

        if status.runStatus == "completed" {
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

        if status.runStatus == "failed" {
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

    private func normalizedNewsListPreferencePrompt() -> String? {
        let trimmed = newsListPreferencePrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func handleAudioError(_ error: Error) {
        errorMessage = error.localizedDescription
        if let dictationError = error as? VoiceDictationError {
            switch dictationError {
            case .noMicrophoneAccess:
                hasMicPermissionDenied = true
                audioState = .error
            default:
                hasDictationError = true
                audioState = .error
            }
        } else {
            hasDictationError = true
            audioState = .error
        }
        stopAudioTimer()
    }

    private func handleDiscoveryTimeout() {
        hasReachedDiscoveryPollingLimit = true
        discoveryErrorMessage =
            "Discovery is taking longer than expected. You can keep waiting, try again, or use defaults."
        errorMessage = nil
        persistProgress()
    }

    private func clearDiscoveryState() {
        pollingTask?.cancel()
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
        isSubmittingAudioDiscovery = false
        onboardingStateStore.clearProgress(userId: user.id)
    }

    private func stopAudioCapture() {
        dictationService.cancel()
        stopAudioTimer()
        audioState = .idle
    }

    private func configureDictationCallbacks() {
        dictationService.onTranscriptDelta = nil
        dictationService.onTranscriptFinal = { [weak self] transcript in
            Task { @MainActor in
                guard let self else { return }
                guard self.step == .audio else { return }
                guard self.audioState == .recording || self.audioState == .transcribing else {
                    return
                }

                let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else {
                    self.errorMessage = "No speech detected. Please try again."
                    self.hasDictationError = true
                    self.audioState = .error
                    self.stopAudioTimer()
                    return
                }

                self.audioState = .transcribing
                self.stopAudioTimer()
                await self.beginDiscovery(transcript: trimmed)
            }
        }
        dictationService.onStateChange = nil

        dictationService.onStopReason = { [weak self] reason in
            Task { @MainActor in
                self?.handleDictationStopReason(reason)
            }
        }

        dictationService.onError = { [weak self] message in
            Task { @MainActor in
                guard let self else { return }
                guard self.step == .audio else { return }
                self.errorMessage = message
                self.hasDictationError = true
                self.audioState = .error
                self.stopAudioTimer()
            }
        }
    }

    private func handleDictationStopReason(_ reason: SpeechStopReason) {
        guard step == .audio else { return }
        switch reason {
        case .manual:
            return
        case .silenceAutoStop:
            return
        case .cancel:
            audioState = .idle
            stopAudioTimer()
        case .failure:
            hasDictationError = true
            audioState = .error
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
        if audioDurationSeconds >= 30 && audioState == .recording {
            Task { await stopAudioCaptureAndDiscover() }
        }
    }

    private func stopAudioTimer() {
        audioTimer?.invalidate()
        audioTimer = nil
    }

    private var suggestionsAreEmpty: Bool {
        substackSuggestions.isEmpty && podcastSuggestions.isEmpty && subredditSuggestions.isEmpty
    }

    private func isDiscoveryTerminalStatus(_ status: String?) -> Bool {
        status == "completed" || status == "failed"
    }

    private func restoreProgress(_ snapshot: OnboardingProgressSnapshot) {
        step = snapshot.step
        isPersonalized = snapshot.isPersonalized
        suggestions = snapshot.suggestions
        selectedSourceKeys = Set(snapshot.selectedSourceKeys)
        selectedSubreddits = Set(snapshot.selectedSubreddits)
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

        guard step == .loading || step == .suggestions else {
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
