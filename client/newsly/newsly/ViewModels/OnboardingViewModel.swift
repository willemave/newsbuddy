//
//  OnboardingViewModel.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import Foundation

enum OnboardingStep: Int {
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
    @Published var topicSummary: String?
    @Published var inferredTopics: [String] = []
    @Published var twitterUsername: String = ""
    @Published var newsDigestPreferencePrompt: String = ""

    private let service = OnboardingService.shared
    private let dictationService = VoiceDictationService.shared
    private let onboardingStateStore = OnboardingStateStore.shared
    private let user: User
    private var audioTimer: Timer?
    private var pollingTask: Task<Void, Never>?
    private var didAutoStartRecording = false
    private var didAttemptResume = false
    private var isSubmittingAudioDiscovery = false

    init(user: User) {
        self.user = user
        self.twitterUsername = user.twitterUsername ?? ""
        self.newsDigestPreferencePrompt = user.newsDigestPreferencePrompt
    }

    deinit {
        pollingTask?.cancel()
        audioTimer?.invalidate()
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

    func advanceToChoice() {
        step = .choice
    }

    func chooseDefaults() {
        isPersonalized = false
        stopAudioCapture()
        clearDiscoveryState()
        Task { await completeOnboarding() }
    }

    func startPersonalized() {
        isPersonalized = true
        step = .audio
        resetAudioState()
    }

    func resumeDiscoveryIfNeeded() async {
        guard !didAttemptResume else { return }
        didAttemptResume = true

        guard let runId = onboardingStateStore.discoveryRunId(userId: user.id) else { return }
        discoveryRunId = runId
        step = .loading
        await refreshDiscoveryStatus(runId: runId)
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
    }

    func toggleSubreddit(_ suggestion: OnboardingSuggestion) {
        guard let subreddit = suggestion.subreddit, !subreddit.isEmpty else { return }
        if selectedSubreddits.contains(subreddit) {
            selectedSubreddits.remove(subreddit)
        } else {
            selectedSubreddits.insert(subreddit)
        }
    }

    func completeOnboarding() async {
        errorMessage = nil
        isLoading = true
        loadingMessage = "Setting up your inbox"
        defer { isLoading = false }

        do {
            let selectedSources = buildSelectedSources()
            let selectedSubreddits = Array(self.selectedSubreddits)
            let request = OnboardingCompleteRequest(
                selectedSources: selectedSources,
                selectedSubreddits: selectedSubreddits,
                profileSummary: isPersonalized ? topicSummary : nil,
                inferredTopics: isPersonalized ? inferredTopics : nil,
                twitterUsername: normalizedTwitterUsername(),
                newsDigestPreferencePrompt: normalizedNewsDigestPreferencePrompt()
            )
            let response = try await service.complete(request: request)
            completionResponse = response
            onboardingStateStore.clearDiscoveryRun(userId: user.id)
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
            onboardingStateStore.setDiscoveryRun(userId: user.id, runId: response.runId)
            step = .loading
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

                try? await Task.sleep(nanoseconds: 2_000_000_000)
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
            onboardingStateStore.clearDiscoveryRun(userId: user.id)
            if let suggestions = status.suggestions {
                applySuggestions(suggestions)
            }
            errorMessage = nil
            step = .suggestions
        } else if status.runStatus == "failed" {
            suggestions = nil
            errorMessage = status.errorMessage ?? "Discovery failed. We'll start you with defaults."
            step = .suggestions
            onboardingStateStore.clearDiscoveryRun(userId: user.id)
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

    private func normalizedNewsDigestPreferencePrompt() -> String? {
        let trimmed = newsDigestPreferencePrompt.trimmingCharacters(in: .whitespacesAndNewlines)
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
        discoveryErrorMessage = "Discovery is taking longer than expected."
        suggestions = nil
        errorMessage = "Discovery is taking longer than expected. We'll start you with defaults."
        onboardingStateStore.clearDiscoveryRun(userId: user.id)
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
        onboardingStateStore.clearDiscoveryRun(userId: user.id)
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
}
