//
//  LearningDeckFocusRecorder.swift
//  newsly
//

import Foundation
import Observation
import SwiftUI

@MainActor
@Observable
final class LearningDeckFocusRecorder {
    private(set) var isRecording = false
    private(set) var isTranscribing = false
    private(set) var isVoiceActionInFlight = false
    var errorMessage: String?

    @ObservationIgnored
    private let transcriptionService: any SpeechTranscribing
    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let refreshTranscriptionAvailability: () async -> Bool
    @ObservationIgnored
    private var voiceDictationAvailable = false
    @ObservationIgnored
    private var pendingTranscript: String?
    @ObservationIgnored
    private var transcriptHandler: ((String) -> Void)?
    @ObservationIgnored
    private var hasConfiguredCallbacks = false

    init(
        transcriptionService: (any SpeechTranscribing)? = nil,
        refreshTranscriptionAvailability: @escaping () async -> Bool
    ) {
        let resolvedTranscriptionService = transcriptionService
            ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.transcriptionService = resolvedTranscriptionService
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: resolvedTranscriptionService)
        self.refreshTranscriptionAvailability = refreshTranscriptionAvailability
    }

    func toggleRecording(onTranscript: @escaping (String) -> Void) async {
        guard !isVoiceActionInFlight, !isTranscribing else { return }
        transcriptHandler = onTranscript

        if isRecording {
            await stopRecording()
        } else {
            await startRecording()
        }
    }

    func cancelRecording() {
        guard hasConfiguredCallbacks || isRecording || isTranscribing || pendingTranscript != nil else {
            return
        }
        voiceCoordinator.stopListening()
        transcriptionService.reset()
        hasConfiguredCallbacks = false
        pendingTranscript = nil
        transcriptHandler = nil
        isRecording = false
        isTranscribing = false
        isVoiceActionInFlight = false
    }

    private func startRecording() async {
        if !voiceDictationAvailable {
            await checkAndRefreshVoiceDictation()
        }
        guard voiceDictationAvailable else {
            errorMessage = "Microphone is unavailable right now. Try again in a moment."
            return
        }

        configureTranscriptionCallbacks()
        pendingTranscript = nil
        errorMessage = nil
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            try await transcriptionService.start()
            isRecording = true
            isTranscribing = false
        } catch {
            errorMessage = error.localizedDescription
            isRecording = false
            isTranscribing = false
        }
    }

    private func stopRecording() async {
        guard isRecording else { return }
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            let transcript = try await transcriptionService.stop()
            pendingTranscript = nil
            isRecording = false
            isTranscribing = false
            applyTranscript(transcript)
        } catch {
            errorMessage = error.localizedDescription
            pendingTranscript = nil
            isRecording = false
            isTranscribing = false
        }
    }

    private func checkAndRefreshVoiceDictation() async {
        if transcriptionService.isAvailable {
            voiceDictationAvailable = true
            return
        }

        voiceDictationAvailable = await refreshTranscriptionAvailability()
    }

    private func configureTranscriptionCallbacks() {
        hasConfiguredCallbacks = true
        voiceCoordinator.listen(
            onTranscriptFinal: { [weak self] transcript in
                self?.pendingTranscript = transcript
            },
            onError: { [weak self] message in
                self?.errorMessage = message
                self?.pendingTranscript = nil
                self?.isRecording = false
                self?.isTranscribing = false
                self?.isVoiceActionInFlight = false
            },
            onStopReason: { [weak self] reason in
                self?.handleTranscriptionStopReason(reason)
            }
        )
    }

    private func handleTranscriptionStopReason(_ reason: SpeechStopReason) {
        switch reason {
        case .manual:
            return
        case .silenceAutoStop:
            let transcript = pendingTranscript ?? ""
            pendingTranscript = nil
            isRecording = false
            isTranscribing = false
            isVoiceActionInFlight = false
            applyTranscript(transcript)
        case .cancel, .failure:
            pendingTranscript = nil
            isRecording = false
            isTranscribing = false
            isVoiceActionInFlight = false
        }
    }

    private func applyTranscript(_ transcript: String) {
        let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTranscript.isEmpty else {
            errorMessage = "I didn't catch that. Try again."
            return
        }

        errorMessage = nil
        transcriptHandler?(trimmedTranscript)
    }
}
