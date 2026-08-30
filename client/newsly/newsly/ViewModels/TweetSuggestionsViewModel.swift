//
//  TweetSuggestionsViewModel.swift
//  newsly
//
//  ViewModel for tweet suggestions sheet.
//

import Foundation
import Observation
import os.log
import UIKit

private let logger = Logger(subsystem: "com.newsly", category: "TweetSuggestions")

protocol TweetSuggestionContentServicing: AnyObject {
    func generateTweetSuggestions(
        id: Int,
        message: String?,
        creativity: Int,
        provider: ChatModelProvider?
    ) async throws -> TweetSuggestionsResponse
}

@MainActor
protocol TweetSharing: AnyObject {
    func share(tweet: String, completion: ((Bool) -> Void)?)
}

extension ContentService: TweetSuggestionContentServicing {}
extension TwitterShareService: TweetSharing {}

@MainActor
@Observable
final class TweetSuggestionsViewModel {
    private enum TaskKey: Hashable {
        case creativityDebounce
    }

    // MARK: - Published Properties

    var suggestions: [TweetSuggestion] = []
    var creativity: Int = 5
    var tweakMessage: String = ""
    var isLoading = false
    var isRegenerating = false
    var errorMessage: String?
    var selectedSuggestionId: Int?
    var selectedProvider: ChatModelProvider = .openai

    // Voice dictation state
    private(set) var voiceDictationAvailable = false
    private(set) var voiceState: SpeechTranscriptionState = .idle

    var isRecording: Bool { voiceState == .recording }
    var isTranscribing: Bool { voiceState == .transcribing }

    var hasVoiceError: Bool {
        if case .failed = voiceState { return true }
        return false
    }

    // MARK: - Private Properties

    @ObservationIgnored
    private let contentService: any TweetSuggestionContentServicing
    @ObservationIgnored
    private let twitterService: any TweetSharing
    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let refreshTranscriptionAvailability: () async -> Bool
    @ObservationIgnored
    private let setBackendTranscriptionAvailable: (Bool) -> Void
    @ObservationIgnored
    private var contentId: Int?
    @ObservationIgnored
    private let tasks = TaskBag<TaskKey>()
    @ObservationIgnored
    private var lastCreativity: Int = 5
    @ObservationIgnored
    private var voiceRecordingStartedAt: Date?
    @ObservationIgnored
    private var pendingVoiceTranscript: String?
    @ObservationIgnored
    private var suggestionRequestGeneration = 0

    // MARK: - Public Methods

    init(
        contentService: any TweetSuggestionContentServicing,
        twitterService: any TweetSharing,
        transcriptionService: any SpeechTranscribing,
        refreshTranscriptionAvailability: @escaping () async -> Bool,
        setBackendTranscriptionAvailable: @escaping (Bool) -> Void
    ) {
        self.contentService = contentService
        self.twitterService = twitterService
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: transcriptionService)
        self.refreshTranscriptionAvailability = refreshTranscriptionAvailability
        self.setBackendTranscriptionAvailable = setBackendTranscriptionAvailable
    }

    deinit {
        tasks.cancelAll()
        MainActor.assumeIsolated {
            voiceCoordinator.cancel()
        }
    }

    /// Initialize with content ID and generate suggestions.
    func initialize(contentId: Int) async {
        suggestionRequestGeneration += 1
        let generation = suggestionRequestGeneration
        self.contentId = contentId
        lastCreativity = creativity

        // Check voice dictation availability and refresh the session if needed
        await checkAndRefreshVoiceDictation()

        guard generation == suggestionRequestGeneration, self.contentId == contentId else {
            return
        }
        await generateSuggestions()
    }

    /// Check voice dictation availability. The shared credential session acquires
    /// or refreshes credentials as part of the availability request.
    private func checkAndRefreshVoiceDictation() async {
        voiceDictationAvailable = await refreshTranscriptionAvailability()
        if voiceDictationAvailable {
            logger.info("🎤 Voice dictation available")
        } else {
            logger.warning("🎤 Voice dictation unavailable because backend transcription is disabled")
            setBackendTranscriptionAvailable(false)
        }
    }

    /// Check and update voice dictation availability (synchronous, for manual refresh).
    func checkVoiceDictationAvailability() {
        voiceDictationAvailable = isVoiceDictationAvailable
    }

    /// Called when creativity slider changes - debounces and auto-regenerates.
    func creativityChanged(to newValue: Int) {
        guard newValue != lastCreativity else { return }

        // Debounce: wait 500ms after user stops sliding before regenerating
        tasks.runReplacing(.creativityDebounce) { [weak self] in
            try? await Task.sleep(nanoseconds: 500_000_000) // 500ms

            guard let self, !Task.isCancelled else { return }

            self.lastCreativity = newValue
            await self.regenerate()
        }
    }

    /// Switch to a different LLM provider and regenerate.
    func switchProvider(to provider: ChatModelProvider) async {
        guard provider != selectedProvider else { return }
        selectedProvider = provider
        await regenerate()
    }

    /// Generate tweet suggestions.
    func generateSuggestions() async {
        await requestSuggestions(isRegeneration: false)
    }

    /// Regenerate suggestions with current settings.
    func regenerate() async {
        await requestSuggestions(isRegeneration: true)
    }

    /// Select a suggestion.
    func selectSuggestion(_ suggestion: TweetSuggestion) {
        selectedSuggestionId = suggestion.id
    }

    /// Share selected suggestion to Twitter.
    func shareToTwitter() {
        guard let selectedId = selectedSuggestionId,
              let suggestion = suggestions.first(where: { $0.id == selectedId }) else {
            return
        }

        twitterService.share(tweet: suggestion.text) { success in
            if success {
                logger.info("Successfully shared tweet")
            } else {
                logger.error("Failed to share tweet")
            }
        }
    }

    /// Share a specific suggestion to Twitter.
    func shareToTwitter(suggestion: TweetSuggestion) {
        twitterService.share(tweet: suggestion.text) { success in
            if success {
                logger.info("Successfully shared tweet")
            } else {
                logger.error("Failed to share tweet")
            }
        }
    }

    /// Copy suggestion text to clipboard.
    func copyToClipboard(suggestion: TweetSuggestion) {
        UIPasteboard.general.string = suggestion.text
        logger.info("Copied tweet to clipboard")
    }

    // MARK: - Voice Dictation

    /// Start voice recording for tweak message.
    func startVoiceRecording() async {
        guard voiceState != .starting, !isRecording, !isTranscribing else { return }
        errorMessage = nil
        let startedAt = Date()
        voiceRecordingStartedAt = startedAt
        logger.info("Tweet suggestion voice recording start requested")
        do {
            try await voiceCoordinator.start(
                onTranscriptFinal: { [weak self] transcript in
                    self?.pendingVoiceTranscript = transcript
                },
                onError: { [weak self] message in
                    self?.applyVoiceFailure(message)
                },
                onStateChange: { [weak self] state in
                    self?.applyVoiceState(state)
                },
                onStopReason: { [weak self] reason in
                    await self?.handleVoiceStopReason(reason)
                }
            )
            logger.info(
                "Tweet suggestion voice recording started | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
        } catch {
            logger.error(
                "Failed to start recording | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            applyVoiceFailure(error.localizedDescription)
        }
    }

    func retryVoiceRecording() async {
        guard hasVoiceError else { return }
        await startVoiceRecording()
    }

    /// Stop recording, transcribe, and auto-regenerate suggestions.
    func stopVoiceRecording() async {
        guard isRecording else { return }

        let startedAt = Date()
        applyVoiceState(.transcribing)
        logger.info(
            "Tweet suggestion voice recording stop requested | captureElapsedMs=\(self.voiceRecordingStartedAt.map { Int(Date().timeIntervalSince($0) * 1000) } ?? 0)"
        )

        do {
            let transcription = try await voiceCoordinator.stop()
            pendingVoiceTranscript = nil
            applyVoiceState(.idle)
            logger.info(
                "Tweet suggestion voice transcription completed | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) transcriptChars=\(transcription.count)"
            )

            await applyVoiceTranscriptAndRegenerate(transcription)
        } catch {
            logger.error(
                "Failed to transcribe | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            applyVoiceFailure(error.localizedDescription)
        }
        voiceRecordingStartedAt = nil
    }

    /// Cancel voice recording.
    func cancelVoiceRecording() {
        voiceCoordinator.cancel()
        pendingVoiceTranscript = nil
        applyVoiceState(.idle)
        if let voiceRecordingStartedAt {
            logger.info(
                "Tweet suggestion voice recording cancelled | captureElapsedMs=\(Int(Date().timeIntervalSince(voiceRecordingStartedAt) * 1000))"
            )
        }
        voiceRecordingStartedAt = nil
    }

    // MARK: - Helpers

    /// Get the creativity label for display.
    var creativityLabel: String {
        switch creativity {
        case 1...3:
            return "Journalist"
        case 4...7:
            return "Insider"
        case 8...10:
            return "Thought Leader"
        default:
            return "Insider"
        }
    }

    /// Check if voice dictation is available.
    var isVoiceDictationAvailable: Bool {
        voiceCoordinator.isAvailable
    }

    private func applyVoiceState(_ state: SpeechTranscriptionState) {
        if case .failed(let message) = state {
            applyVoiceFailure(message)
        } else {
            voiceState = state
        }
    }

    private func applyVoiceFailure(_ message: String) {
        voiceState = .failed(message)
        errorMessage = message
        pendingVoiceTranscript = nil
        voiceRecordingStartedAt = nil
    }

    private func handleVoiceStopReason(_ reason: SpeechStopReason) async {
        switch reason {
        case .manual:
            return
        case .silenceAutoStop, .maximumDuration:
            let transcript = pendingVoiceTranscript ?? ""
            pendingVoiceTranscript = nil
            applyVoiceState(.idle)
            await applyVoiceTranscriptAndRegenerate(transcript)
        case .noSpeechTimeout:
            applyVoiceFailure("No speech detected. Try again.")
        case .cancel:
            pendingVoiceTranscript = nil
            applyVoiceState(.idle)
        case .failure:
            break
        }
    }

    private func applyVoiceTranscriptAndRegenerate(_ transcript: String) async {
        let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            applyVoiceFailure("I didn't catch that. Try again.")
            return
        }
        if tweakMessage.isEmpty {
            tweakMessage = trimmed
        } else {
            tweakMessage += " " + trimmed
        }
        await regenerate()
    }

    private func requestSuggestions(isRegeneration: Bool) async {
        guard let contentId else { return }
        suggestionRequestGeneration += 1
        let generation = suggestionRequestGeneration
        let message = tweakMessage.isEmpty ? nil : tweakMessage
        let requestedCreativity = creativity
        let requestedProvider = selectedProvider

        isLoading = true
        isRegenerating = isRegeneration
        errorMessage = nil
        defer {
            if generation == suggestionRequestGeneration {
                isLoading = false
                isRegenerating = false
            }
        }

        do {
            let response = try await contentService.generateTweetSuggestions(
                id: contentId,
                message: message,
                creativity: requestedCreativity,
                provider: requestedProvider
            )
            guard generation == suggestionRequestGeneration else { return }
            suggestions = response.suggestions
            logger.info("Generated \(response.suggestions.count) tweet suggestions")
        } catch {
            guard generation == suggestionRequestGeneration else { return }
            logger.error("Failed to generate suggestions: \(error.localizedDescription)")
            errorMessage = error.localizedDescription
        }
    }

}
