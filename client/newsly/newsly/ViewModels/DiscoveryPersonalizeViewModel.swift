//
//  DiscoveryPersonalizeViewModel.swift
//  newsly
//
//  Lighter version of OnboardingViewModel focused on voice → discover → select → complete.
//

import Foundation

private let discoveryPersonalizePollingIntervalNanoseconds: UInt64 = 500_000_000

@MainActor
final class DiscoveryPersonalizeViewModel: ObservableObject {
    enum Step: Int {
        case audio
        case loading
        case suggestions
    }

    // MARK: - Published State

    @Published var step: Step = .audio
    @Published var suggestions: OnboardingFastDiscoverResponse?
    @Published var selectedSourceKeys: Set<String> = []
    @Published var selectedSubreddits: Set<String> = []
    @Published var isLoading = false
    @Published var loadingMessage = ""
    @Published var errorMessage: String?

    @Published var audioState: OnboardingAudioState = .idle
    @Published var audioDurationSeconds: Int = 0

    @Published var discoveryLanes: [OnboardingDiscoveryLaneStatus] = []
    @Published var discoveryRunId: Int?
    @Published var discoveryRunStatus: String?
    @Published var discoveryErrorMessage: String?
    @Published var topicSummary: String?
    @Published var inferredTopics: [String] = []

    var onComplete: (() -> Void)?

    // MARK: - Dependencies

    private let service = OnboardingService.shared
    private let dictationService: any SpeechTranscribing
    private let onboardingStateStore = OnboardingStateStore.shared
    private let userId: Int
    private var audioTimer: Timer?
    private var pollingTask: Task<Void, Never>?
    private var isSubmittingAudioDiscovery = false
    private var didAutoStartRecording = false
    private var didAttemptResume = false

    init(userId: Int) {
        self.userId = userId
        self.dictationService = SpeechTranscriberFactory.makeVoiceDictationTranscriber()
    }

    deinit {
        pollingTask?.cancel()
        audioTimer?.invalidate()
        let service = dictationService
        Task { @MainActor in service.cancel() }
    }

    // MARK: - Computed Helpers

    var substackSuggestions: [OnboardingSuggestion] {
        suggestions?.recommendedSubstacks ?? []
    }

    var podcastSuggestions: [OnboardingSuggestion] {
        suggestions?.recommendedPods ?? []
    }

    var subredditSuggestions: [OnboardingSuggestion] {
        suggestions?.recommendedSubreddits ?? []
    }

    // MARK: - Audio

    func startAudioCaptureIfNeeded() async {
        guard !didAutoStartRecording else { return }
        didAutoStartRecording = true
        await startAudioCapture()
    }

    func startAudioCapture() async {
        configureDictationCallbacks()
        errorMessage = nil
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

    func skipToDefaults() {
        stopAudioCapture()
        clearDiscoveryState()
        Task { await completePersonalization() }
    }

    func cancelPersonalization() {
        stopAudioCapture()
        clearDiscoveryState()
        errorMessage = nil
    }

    // MARK: - Discovery

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
            onboardingStateStore.setDiscoveryRun(userId: userId, runId: response.runId)
            step = .loading
            startPolling(runId: response.runId)
        } catch {
            errorMessage = error.localizedDescription
            audioState = .error
        }
    }

    func resumeDiscoveryIfNeeded() async {
        guard !didAttemptResume else { return }
        didAttemptResume = true

        guard let runId = onboardingStateStore.discoveryRunId(userId: userId) else { return }
        discoveryRunId = runId
        step = .loading
        await refreshDiscoveryStatus(runId: runId)

        if let status = discoveryRunStatus, status == "completed" || status == "failed" {
            return
        }
        startPolling(runId: runId)
    }

    private func refreshDiscoveryStatus(runId: Int) async {
        do {
            let status = try await service.discoveryStatus(runId: runId)
            applyDiscoveryStatus(status)
        } catch {
            discoveryErrorMessage = error.localizedDescription
        }
    }

    private func startPolling(runId: Int) {
        pollingTask?.cancel()
        pollingTask = Task { @MainActor in
            let deadline = Date().addingTimeInterval(60)
            while !Task.isCancelled {
                await refreshDiscoveryStatus(runId: runId)

                if let status = discoveryRunStatus, status == "completed" || status == "failed" {
                    break
                }

                if Date() >= deadline {
                    handleDiscoveryTimeout()
                    break
                }

                try? await Task.sleep(nanoseconds: discoveryPersonalizePollingIntervalNanoseconds)
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

        if status.runStatus == "completed" {
            onboardingStateStore.clearDiscoveryRun(userId: userId)
            if let suggestions = status.suggestions {
                applySuggestions(suggestions)
            }
            errorMessage = nil
            step = .suggestions
        } else if status.runStatus == "failed" {
            suggestions = nil
            errorMessage = status.errorMessage ?? "Discovery failed."
            step = .suggestions
            onboardingStateStore.clearDiscoveryRun(userId: userId)
        }
    }

    private func applySuggestions(_ response: OnboardingFastDiscoverResponse) {
        suggestions = response
        let sourceKeys = (response.recommendedSubstacks + response.recommendedPods)
            .compactMap { $0.feedURL }
        selectedSourceKeys = Set(sourceKeys)
        let subredditKeys = response.recommendedSubreddits.compactMap { $0.subreddit }
        selectedSubreddits = Set(subredditKeys)
    }

    // MARK: - Selection

    func toggleSource(_ suggestion: OnboardingSuggestion) {
        guard let feedURL = suggestion.feedURL, !feedURL.isEmpty else { return }
        if selectedSourceKeys.contains(feedURL) {
            selectedSourceKeys.remove(feedURL)
        } else {
            selectedSourceKeys.insert(feedURL)
        }
    }

    func toggleSubreddit(_ suggestion: OnboardingSuggestion) {
        guard let subreddit = suggestion.subreddit, !subreddit.isEmpty else { return }
        if selectedSubreddits.contains(subreddit) {
            selectedSubreddits.remove(subreddit)
        } else {
            selectedSubreddits.insert(subreddit)
        }
    }

    // MARK: - Complete

    func completePersonalization() async {
        errorMessage = nil
        isLoading = true
        loadingMessage = "Adding to your feeds"
        defer { isLoading = false }

        do {
            let selectedSources = buildSelectedSources()
            let selectedSubreddits = Array(self.selectedSubreddits)
            let request = OnboardingCompleteRequest(
                selectedSources: selectedSources,
                selectedSubreddits: selectedSubreddits,
                selectedAggregators: [],
                profileSummary: topicSummary,
                inferredTopics: inferredTopics.isEmpty ? nil : inferredTopics,
                twitterUsername: nil,
                newsListPreferencePrompt: nil
            )
            _ = try await service.complete(request: request)
            onboardingStateStore.clearDiscoveryRun(userId: userId)
            onComplete?()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    // MARK: - Private Helpers

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

    private func handleAudioError(_ error: Error) {
        errorMessage = error.localizedDescription
        audioState = .error
        stopAudioTimer()
    }

    private func handleDiscoveryTimeout() {
        discoveryErrorMessage = "Discovery is taking longer than expected."
        suggestions = nil
        errorMessage = "Discovery timed out."
        onboardingStateStore.clearDiscoveryRun(userId: userId)
        step = .suggestions
    }

    private func clearDiscoveryState() {
        pollingTask?.cancel()
        discoveryRunId = nil
        discoveryRunStatus = nil
        discoveryLanes = []
        discoveryErrorMessage = nil
        topicSummary = nil
        inferredTopics = []
        suggestions = nil
        selectedSourceKeys = []
        selectedSubreddits = []
        isSubmittingAudioDiscovery = false
        onboardingStateStore.clearDiscoveryRun(userId: userId)
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
                guard self.audioState == .recording || self.audioState == .transcribing else { return }

                let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else {
                    self.errorMessage = "No speech detected. Please try again."
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
                guard let self else { return }
                switch reason {
                case .manual, .silenceAutoStop:
                    break
                case .cancel:
                    self.audioState = .idle
                    self.stopAudioTimer()
                case .failure:
                    self.audioState = .error
                    self.stopAudioTimer()
                }
            }
        }

        dictationService.onError = { [weak self] message in
            Task { @MainActor in
                guard let self else { return }
                guard self.step == .audio else { return }
                self.errorMessage = message
                self.audioState = .error
                self.stopAudioTimer()
            }
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
}
