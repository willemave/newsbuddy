//
//  DiscoveryPersonalizeViewModel.swift
//  newsly
//
//  Lighter version of OnboardingViewModel focused on voice → discover → select → complete.
//

import Foundation
import Observation
import os.log

private let discoveryPersonalizePollingIntervalNanoseconds: UInt64 = 500_000_000
private let discoveryPersonalizeVoiceLogger = Logger(
    subsystem: "com.newsly",
    category: "DiscoveryPersonalizeVoice"
)

private func discoveryPersonalizeElapsedMilliseconds(since start: Date) -> Int {
    Int(Date().timeIntervalSince(start) * 1000)
}

@MainActor
@Observable
final class DiscoveryPersonalizeViewModel {
    private enum TaskKey: Hashable {
        case discoveryPolling
    }

    enum Step: Int {
        case audio
        case loading
        case suggestions
    }

    // MARK: - Published State

    var step: Step = .audio
    var suggestions: OnboardingFastDiscoverResponse?
    var selectedSourceKeys: Set<String> = []
    var selectedSubreddits: Set<String> = []
    var isLoading = false
    var loadingMessage = ""
    var errorMessage: String?

    var audioState: OnboardingAudioState = .idle
    var audioDurationSeconds: Int = 0

    var discoveryLanes: [OnboardingDiscoveryLaneStatus] = []
    var discoveryRunId: Int?
    var discoveryRunStatus: String?
    var discoveryErrorMessage: String?
    var topicSummary: String?
    var inferredTopics: [String] = []

    @ObservationIgnored
    var onComplete: (() -> Void)?

    // MARK: - Dependencies

    @ObservationIgnored
    private let service: OnboardingService
    @ObservationIgnored
    private let dictationService: any SpeechTranscribing
    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let onboardingStateStore: OnboardingStateStore
    @ObservationIgnored
    private let userId: Int
    @ObservationIgnored
    private var audioTimer: Timer?
    @ObservationIgnored
    private let tasks = TaskBag<TaskKey>()
    @ObservationIgnored
    private var isSubmittingAudioDiscovery = false
    @ObservationIgnored
    private var didAutoStartRecording = false
    @ObservationIgnored
    private var didAttemptResume = false
    @ObservationIgnored
    private var audioCaptureStartedAt: Date?

    init(
        userId: Int,
        service: OnboardingService,
        dictationService: (any SpeechTranscribing)? = nil,
        onboardingStateStore: OnboardingStateStore
    ) {
        self.userId = userId
        self.service = service
        let resolvedDictationService = dictationService
            ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.dictationService = resolvedDictationService
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: resolvedDictationService)
        self.onboardingStateStore = onboardingStateStore
    }

    deinit {
        tasks.cancelAll()
        audioTimer?.invalidate()
        let service = dictationService
        let coordinator = voiceCoordinator
        Task { @MainActor in
            coordinator.stopListening()
            service.cancel()
        }
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
        let startedAt = Date()
        configureDictationCallbacks()
        errorMessage = nil
        audioState = .recording
        audioCaptureStartedAt = startedAt
        startAudioTimer()
        discoveryPersonalizeVoiceLogger.info("Discovery personalize audio capture start requested")

        do {
            try await dictationService.start()
            discoveryPersonalizeVoiceLogger.info(
                "Discovery personalize audio capture started | elapsedMs=\(discoveryPersonalizeElapsedMilliseconds(since: startedAt))"
            )
        } catch {
            discoveryPersonalizeVoiceLogger.error(
                "Discovery personalize audio capture failed | elapsedMs=\(discoveryPersonalizeElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            handleAudioError(error)
        }
    }

    func stopAudioCaptureAndDiscover() async {
        guard audioState == .recording else { return }
        let startedAt = Date()
        audioState = .transcribing
        stopAudioTimer()
        discoveryPersonalizeVoiceLogger.info(
            "Discovery personalize audio capture stop requested | captureElapsedMs=\(self.audioCaptureStartedAt.map { discoveryPersonalizeElapsedMilliseconds(since: $0) } ?? 0)"
        )
        do {
            _ = try await dictationService.stop()
            discoveryPersonalizeVoiceLogger.info(
                "Discovery personalize audio capture stopped | elapsedMs=\(discoveryPersonalizeElapsedMilliseconds(since: startedAt))"
            )
        } catch {
            discoveryPersonalizeVoiceLogger.error(
                "Discovery personalize audio capture stop failed | elapsedMs=\(discoveryPersonalizeElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            handleAudioError(error)
        }
    }

    func skipPersonalization() {
        stopAudioCapture()
        clearDiscoveryState()
        Task { await completePersonalization() }
    }

    func cancelPersonalization() {
        stopAudioCapture()
        clearDiscoveryState()
        errorMessage = nil
    }

    func handleDisappear() {
        stopAudioCapture()
        tasks.cancel(.discoveryPolling)
    }

    // MARK: - Discovery

    private func beginDiscovery(transcript: String) async {
        guard !isSubmittingAudioDiscovery else { return }
        let startedAt = Date()
        isSubmittingAudioDiscovery = true
        defer {
            isSubmittingAudioDiscovery = false
            audioCaptureStartedAt = nil
        }

        do {
            discoveryPersonalizeVoiceLogger.info(
                "Discovery personalize audio discovery begin | transcriptChars=\(transcript.count)"
            )
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
            discoveryPersonalizeVoiceLogger.info(
                "Discovery personalize audio discovery started | runId=\(response.runId) laneCount=\(response.lanes.count) elapsedMs=\(discoveryPersonalizeElapsedMilliseconds(since: startedAt))"
            )
        } catch {
            discoveryPersonalizeVoiceLogger.error(
                "Discovery personalize audio discovery failed | elapsedMs=\(discoveryPersonalizeElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
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

        if isDiscoveryTerminalStatus(discoveryRunStatus) {
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
        tasks.runReplacing(.discoveryPolling) { [weak self] in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(60)
            while !Task.isCancelled {
                await self.refreshDiscoveryStatus(runId: runId)

                if self.isDiscoveryTerminalStatus(self.discoveryRunStatus) {
                    break
                }

                if Date() >= deadline {
                    self.handleDiscoveryTimeout()
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

        if discoveryTaskStatus(status.runStatus) == .completed {
            onboardingStateStore.clearDiscoveryRun(userId: userId)
            if let suggestions = status.suggestions {
                applySuggestions(suggestions)
            }
            errorMessage = nil
            step = .suggestions
        } else if discoveryTaskStatus(status.runStatus) == .failed {
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
                twitterUsername: nil
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

    private func discoveryTaskStatus(_ status: String?) -> APITaskStatus? {
        guard let status else { return nil }
        return APITaskStatus(rawValue: status)
    }

    private func isDiscoveryTerminalStatus(_ status: String?) -> Bool {
        let taskStatus = discoveryTaskStatus(status)
        return taskStatus == .completed || taskStatus == .failed
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
        tasks.cancel(.discoveryPolling)
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
        voiceCoordinator.stopListening()
        stopAudioTimer()
        audioState = .idle
        if let audioCaptureStartedAt {
            discoveryPersonalizeVoiceLogger.info(
                "Discovery personalize audio capture cancelled | captureElapsedMs=\(discoveryPersonalizeElapsedMilliseconds(since: audioCaptureStartedAt))"
            )
        }
        audioCaptureStartedAt = nil
    }

    private func configureDictationCallbacks() {
        voiceCoordinator.listen(
            onTranscriptFinal: { [weak self] transcript in
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
                discoveryPersonalizeVoiceLogger.info(
                    "Discovery personalize transcript final | transcriptChars=\(trimmed.count) captureElapsedMs=\(self.audioCaptureStartedAt.map { discoveryPersonalizeElapsedMilliseconds(since: $0) } ?? 0)"
                )
                await self.beginDiscovery(transcript: trimmed)
            },
            onError: { [weak self] message in
                guard let self else { return }
                guard self.step == .audio else { return }
                self.errorMessage = message
                self.audioState = .error
                self.stopAudioTimer()
            },
            onStopReason: { [weak self] reason in
                self?.handleDictationStopReason(reason)
            }
        )
    }

    private func handleDictationStopReason(_ reason: SpeechStopReason) {
        switch reason {
        case .manual, .silenceAutoStop:
            break
        case .cancel:
            audioState = .idle
            stopAudioTimer()
        case .failure:
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
