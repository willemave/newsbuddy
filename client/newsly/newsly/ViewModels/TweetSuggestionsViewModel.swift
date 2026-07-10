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
    var isRecording = false
    var isTranscribing = false
    private(set) var voiceDictationAvailable = false

    // MARK: - Private Properties

    @ObservationIgnored
    private let contentService: any TweetSuggestionContentServicing
    @ObservationIgnored
    private let twitterService: any TweetSharing
    @ObservationIgnored
    private let transcriptionService: any SpeechTranscribing
    @ObservationIgnored
    private let authService: any AuthenticationServicing
    @ObservationIgnored
    private let tokenStore: any AuthTokenStore
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

    // MARK: - Public Methods

    init(
        contentService: any TweetSuggestionContentServicing,
        twitterService: any TweetSharing,
        transcriptionService: any SpeechTranscribing,
        authService: any AuthenticationServicing,
        tokenStore: any AuthTokenStore,
        refreshTranscriptionAvailability: @escaping () async -> Bool,
        setBackendTranscriptionAvailable: @escaping (Bool) -> Void
    ) {
        self.contentService = contentService
        self.twitterService = twitterService
        self.transcriptionService = transcriptionService
        self.authService = authService
        self.tokenStore = tokenStore
        self.refreshTranscriptionAvailability = refreshTranscriptionAvailability
        self.setBackendTranscriptionAvailable = setBackendTranscriptionAvailable
    }

    deinit {
        tasks.cancelAll()
    }

    /// Initialize with content ID and generate suggestions.
    func initialize(contentId: Int) async {
        self.contentId = contentId
        lastCreativity = creativity

        // Check voice dictation availability and refresh the session if needed
        await checkAndRefreshVoiceDictation()

        await generateSuggestions()
    }

    /// Check voice dictation availability and attempt token refresh if auth is stale.
    private func checkAndRefreshVoiceDictation() async {
        do {
            if !hasVoiceAuthToken {
                logger.info("🎤 Voice dictation unavailable, attempting session refresh...")
                _ = try await authService.refreshAccessToken()
            }
            voiceDictationAvailable = await refreshTranscriptionAvailability()
            if voiceDictationAvailable {
                logger.info("🎤 Voice dictation available")
            } else {
                logger.warning("🎤 Voice dictation unavailable because backend transcription is disabled")
            }
        } catch {
            logger.warning("🎤 Token refresh failed: \(error.localizedDescription)")
            setBackendTranscriptionAvailable(false)
            voiceDictationAvailable = false
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
        guard let contentId = contentId else { return }

        isLoading = true
        errorMessage = nil

        do {
            let message = tweakMessage.isEmpty ? nil : tweakMessage
            let response = try await contentService.generateTweetSuggestions(
                id: contentId,
                message: message,
                creativity: creativity,
                provider: selectedProvider
            )
            suggestions = response.suggestions
            logger.info("Generated \(response.suggestions.count) tweet suggestions")
        } catch {
            logger.error("Failed to generate suggestions: \(error.localizedDescription)")
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    /// Regenerate suggestions with current settings.
    func regenerate() async {
        isRegenerating = true
        await generateSuggestions()
        isRegenerating = false
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
        guard !isRecording, !isTranscribing else { return }
        let startedAt = Date()
        voiceRecordingStartedAt = startedAt
        logger.info("Tweet suggestion voice recording start requested")
        do {
            try await transcriptionService.start()
            isRecording = true
            logger.info(
                "Tweet suggestion voice recording started | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
        } catch {
            logger.error(
                "Failed to start recording | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            errorMessage = error.localizedDescription
        }
    }

    /// Stop recording, transcribe, and auto-regenerate suggestions.
    func stopVoiceRecording() async {
        guard isRecording else { return }

        let startedAt = Date()
        isRecording = false
        isTranscribing = true
        logger.info(
            "Tweet suggestion voice recording stop requested | captureElapsedMs=\(self.voiceRecordingStartedAt.map { Int(Date().timeIntervalSince($0) * 1000) } ?? 0)"
        )

        do {
            let transcription = try await transcriptionService.stop()
            // Append to existing tweak message
            if tweakMessage.isEmpty {
                tweakMessage = transcription
            } else {
                tweakMessage += " " + transcription
            }
            isTranscribing = false
            logger.info(
                "Tweet suggestion voice transcription completed | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) transcriptChars=\(transcription.count)"
            )

            // Auto-regenerate with the new tweak message
            await regenerate()
        } catch {
            logger.error(
                "Failed to transcribe | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            errorMessage = error.localizedDescription
            isTranscribing = false
        }
        voiceRecordingStartedAt = nil
    }

    /// Cancel voice recording.
    func cancelVoiceRecording() {
        transcriptionService.cancel()
        isRecording = false
        isTranscribing = false
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
        transcriptionService.isAvailable
    }

    private var hasVoiceAuthToken: Bool {
        if let accessToken = tokenStore.getToken(key: .accessToken), !accessToken.isEmpty {
            return true
        }
        if let refreshToken = tokenStore.getToken(key: .refreshToken), !refreshToken.isEmpty {
            return true
        }
        return false
    }
}
