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
    private(set) var isAvailable: Bool
    private(set) var isActionInFlight = false
    private(set) var state: SpeechTranscriptionState = .idle

    var isRecording: Bool { state == .recording }
    var isTranscribing: Bool { state == .transcribing }

    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let refreshAvailability: () async -> Bool
    @ObservationIgnored
    private let setBackendAvailability: (Bool) -> Void
    @ObservationIgnored
    private var onTranscriptReady: (@MainActor (String) async -> Void)?
    @ObservationIgnored
    private var errorHandler: (@MainActor (String) -> Void)?
    @ObservationIgnored
    private var pendingTranscript: String?
    @ObservationIgnored
    private var hasSubmittedTranscript = false
    @ObservationIgnored
    private var hasReportedTerminalError = false
    @ObservationIgnored
    private var recordingStartedAt: Date?

    init(
        transcriptionService: any SpeechTranscribing,
        refreshAvailability: @escaping () async -> Bool,
        setBackendAvailability: @escaping (Bool) -> Void,
        initiallyAvailable: Bool
    ) {
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: transcriptionService)
        self.refreshAvailability = refreshAvailability
        self.setBackendAvailability = setBackendAvailability
        self.isAvailable = initiallyAvailable
    }

    func configure(
        onTranscriptReady: @escaping @MainActor (String) async -> Void,
        onError: @escaping @MainActor (String) -> Void
    ) {
        self.onTranscriptReady = onTranscriptReady
        errorHandler = onError
    }

    func checkAndRefreshAvailability() async {
        if voiceCoordinator.isAvailable {
            isAvailable = true
            return
        }

        isAvailable = await refreshAvailability()
        if !isAvailable {
            setBackendAvailability(false)
        }
    }

    func start() async {
        guard !isRecording, !isTranscribing else { return }
        let startedAt = Date()
        hasSubmittedTranscript = false
        hasReportedTerminalError = false
        pendingTranscript = nil

        if !isAvailable {
            await checkAndRefreshAvailability()
        }
        guard isAvailable else {
            chatVoiceLogger.error(
                "Voice recording unavailable | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
            resetCaptureState()
            reportTerminalError("Microphone is unavailable right now. Try again in a moment.")
            return
        }

        recordingStartedAt = Date()
        chatVoiceLogger.info("Starting voice recording")
        do {
            try await voiceCoordinator.start(
                onTranscriptFinal: { [weak self] transcript in
                    self?.pendingTranscript = transcript
                },
                onError: { [weak self] message in
                    guard let self else { return }
                    self.resetCaptureState(state: .failed(message))
                    self.isActionInFlight = false
                    self.reportTerminalError(message)
                },
                onStateChange: { [weak self] state in
                    self?.apply(state)
                },
                onStopReason: { [weak self] reason in
                    await self?.handle(reason)
                }
            )
            chatVoiceLogger.info(
                "Voice recording started | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
            )
        } catch {
            chatVoiceLogger.error(
                "Voice recording start failed | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
            )
            resetCaptureState()
            reportTerminalError(error.localizedDescription)
        }
    }

    func stop() async {
        guard isRecording else { return }
        let startedAt = Date()
        chatVoiceLogger.info(
            "Stopping voice recording | captureElapsedMs=\(self.recordingStartedAt.map { Int(Date().timeIntervalSince($0) * 1000) } ?? 0)"
        )

        do {
            state = .transcribing
            let transcript = try await voiceCoordinator.stop()
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
        voiceCoordinator.cancel()
        isActionInFlight = false
        resetCaptureState()
    }

    private func apply(_ state: SpeechTranscriptionState) {
        self.state = state
    }

    private func handle(_ reason: SpeechStopReason) async {
        switch reason {
        case .manual:
            return
        case .silenceAutoStop, .maximumDuration:
            let transcript = pendingTranscript ?? ""
            resetCaptureState()
            isActionInFlight = true
            await submit(transcript)
            isActionInFlight = false
        case .noSpeechTimeout:
            resetCaptureState(state: .failed("No speech detected. Try again."))
            reportTerminalError("No speech detected. Try again.")
            isActionInFlight = false
        case .cancel:
            resetCaptureState()
            isActionInFlight = false
        case .failure:
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

    private func resetCaptureState(state: SpeechTranscriptionState = .idle) {
        self.state = state
        pendingTranscript = nil
        recordingStartedAt = nil
    }

    private func reportError(_ message: String) {
        errorHandler?(message)
    }

    private func reportTerminalError(_ message: String) {
        guard !hasReportedTerminalError else { return }
        hasReportedTerminalError = true
        reportError(message)
    }
}
