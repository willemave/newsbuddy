//
//  ChatVoiceInputController.swift
//  newsly
//

import Foundation
import Observation
import os

private let chatVoiceLogger = Logger(
    subsystem: "com.newsly",
    category: "ChatVoiceInputController"
)

@MainActor
@Observable
final class ChatVoiceInputController {
    private(set) var isRecording = false
    private(set) var isTranscribing = false
    private(set) var isAvailable: Bool
    private(set) var isActionInFlight = false

    @ObservationIgnored
    private let transcriptionService: any SpeechTranscribing
    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let authService: any AuthenticationServicing
    @ObservationIgnored
    private let tokenStore: any AuthTokenStore
    @ObservationIgnored
    private let refreshAvailability: () async -> Bool
    @ObservationIgnored
    private let setBackendAvailability: (Bool) -> Void
    @ObservationIgnored
    private var onTranscriptReady: (@MainActor (String) async -> Void)?
    @ObservationIgnored
    private var onError: (@MainActor (String) -> Void)?
    @ObservationIgnored
    private var pendingTranscript: String?
    @ObservationIgnored
    private var hasSubmittedTranscript = false
    @ObservationIgnored
    private var recordingStartedAt: Date?
    @ObservationIgnored
    private var isListeningForTranscriptionEvents = false

    init(
        transcriptionService: any SpeechTranscribing,
        authService: any AuthenticationServicing,
        tokenStore: any AuthTokenStore,
        refreshAvailability: @escaping () async -> Bool,
        setBackendAvailability: @escaping (Bool) -> Void,
        initiallyAvailable: Bool
    ) {
        self.transcriptionService = transcriptionService
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: transcriptionService)
        self.authService = authService
        self.tokenStore = tokenStore
        self.refreshAvailability = refreshAvailability
        self.setBackendAvailability = setBackendAvailability
        self.isAvailable = initiallyAvailable
    }

    func configure(
        onTranscriptReady: @escaping @MainActor (String) async -> Void,
        onError: @escaping @MainActor (String) -> Void
    ) {
        self.onTranscriptReady = onTranscriptReady
        self.onError = onError
    }

    func checkAndRefreshAvailability() async {
        if transcriptionService.isAvailable {
            isAvailable = true
            return
        }

        do {
            if !hasAuthToken {
                _ = try await authService.refreshAccessToken()
            }
            isAvailable = await refreshAvailability()
        } catch {
            chatVoiceLogger.debug(
                "Token refresh for voice dictation failed: \(error.localizedDescription)"
            )
            setBackendAvailability(false)
            isAvailable = false
        }
    }

    func start() async {
        guard !isRecording, !isTranscribing else { return }
        let startedAt = Date()
        hasSubmittedTranscript = false
        pendingTranscript = nil

        if !isAvailable {
            await checkAndRefreshAvailability()
        }
        guard isAvailable else {
            chatVoiceLogger.error(
                "Voice recording unavailable | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
            resetCaptureState()
            reportError("Microphone is unavailable right now. Try again in a moment.")
            return
        }

        ensureTranscriptionCallbacks()
        recordingStartedAt = Date()
        chatVoiceLogger.info("Starting voice recording")
        do {
            try await transcriptionService.start()
            isRecording = true
            chatVoiceLogger.info(
                "Voice recording started | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
        } catch {
            chatVoiceLogger.error(
                "Voice recording start failed | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            resetCaptureState()
            reportError(error.localizedDescription)
        }
    }

    func stop() async {
        guard isRecording else { return }
        let startedAt = Date()
        chatVoiceLogger.info(
            "Stopping voice recording | captureElapsedMs=\(self.recordingStartedAt.map { Int(Date().timeIntervalSince($0) * 1000) } ?? 0)"
        )

        do {
            let transcript = try await transcriptionService.stop()
            chatVoiceLogger.info(
                "Transcription complete | length=\(transcript.count) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
            resetCaptureState()
            await submit(transcript)
        } catch {
            chatVoiceLogger.error(
                "Voice transcription error | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            resetCaptureState()
            reportError(error.localizedDescription)
        }
    }

    func toggle() async {
        guard !isActionInFlight, !isTranscribing else { return }

        isActionInFlight = true
        defer { isActionInFlight = false }

        if isRecording {
            await stop()
        } else {
            await start()
        }
    }

    func reset() {
        transcriptionService.reset()
        stopListeningForTranscriptionEvents()
        isActionInFlight = false
        resetCaptureState()
    }

    private var hasAuthToken: Bool {
        if let accessToken = tokenStore.getToken(key: .accessToken), !accessToken.isEmpty {
            return true
        }
        if let refreshToken = tokenStore.getToken(key: .refreshToken), !refreshToken.isEmpty {
            return true
        }
        return false
    }

    private func ensureTranscriptionCallbacks() {
        guard !isListeningForTranscriptionEvents else { return }

        voiceCoordinator.listen(
            onTranscriptFinal: { [weak self] transcript in
                self?.pendingTranscript = transcript
            },
            onError: { [weak self] message in
                guard let self else { return }
                self.resetCaptureState()
                self.isActionInFlight = false
                self.reportError(message)
            },
            onStateChange: { [weak self] state in
                self?.apply(state)
            },
            onStopReason: { [weak self] reason in
                await self?.handle(reason)
            }
        )
        isListeningForTranscriptionEvents = true
    }

    private func stopListeningForTranscriptionEvents() {
        guard isListeningForTranscriptionEvents else { return }
        voiceCoordinator.stopListening()
        isListeningForTranscriptionEvents = false
    }

    private func apply(_ state: SpeechTranscriptionState) {
        switch state {
        case .idle:
            isRecording = false
            isTranscribing = false
        case .recording:
            isRecording = true
            isTranscribing = false
        case .transcribing:
            isRecording = false
            isTranscribing = true
        }
    }

    private func handle(_ reason: SpeechStopReason) async {
        switch reason {
        case .manual:
            return
        case .silenceAutoStop:
            let transcript = pendingTranscript ?? ""
            resetCaptureState()
            isActionInFlight = true
            await submit(transcript)
            isActionInFlight = false
        case .cancel, .failure:
            resetCaptureState()
            isActionInFlight = false
        }
    }

    private func submit(_ transcript: String) async {
        let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTranscript.isEmpty else {
            reportError("I didn't catch that. Try again.")
            return
        }
        guard !hasSubmittedTranscript else { return }

        hasSubmittedTranscript = true
        await onTranscriptReady?(trimmedTranscript)
    }

    private func resetCaptureState() {
        isRecording = false
        isTranscribing = false
        pendingTranscript = nil
        recordingStartedAt = nil
    }

    private func reportError(_ message: String) {
        onError?(message)
    }
}
