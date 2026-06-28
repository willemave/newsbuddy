//
//  LearningDeckFocusRecorder.swift
//  newsly
//

import Foundation
import SwiftUI

@MainActor
final class LearningDeckFocusRecorder: ObservableObject {
    @Published private(set) var isRecording = false
    @Published private(set) var isTranscribing = false
    @Published private(set) var isVoiceActionInFlight = false
    @Published var errorMessage: String?

    private let transcriptionService: any SpeechTranscribing
    private var voiceDictationAvailable = false
    private var pendingTranscript: String?
    private var transcriptHandler: ((String) -> Void)?
    private var hasConfiguredCallbacks = false

    init(transcriptionService: (any SpeechTranscribing)? = nil) {
        self.transcriptionService = transcriptionService
            ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
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

        voiceDictationAvailable = await OpenAIService.shared.refreshTranscriptionAvailability()
    }

    private func configureTranscriptionCallbacks() {
        hasConfiguredCallbacks = true
        transcriptionService.onTranscriptDelta = nil
        transcriptionService.onTranscriptFinal = { [weak self] transcript in
            self?.pendingTranscript = transcript
        }
        transcriptionService.onStopReason = { [weak self] reason in
            guard let self else { return }
            switch reason {
            case .manual:
                return
            case .silenceAutoStop:
                let transcript = self.pendingTranscript ?? ""
                self.pendingTranscript = nil
                self.isRecording = false
                self.isTranscribing = false
                self.isVoiceActionInFlight = false
                self.applyTranscript(transcript)
            case .cancel, .failure:
                self.pendingTranscript = nil
                self.isRecording = false
                self.isTranscribing = false
                self.isVoiceActionInFlight = false
            }
        }
        transcriptionService.onError = { [weak self] message in
            self?.errorMessage = message
            self?.pendingTranscript = nil
            self?.isRecording = false
            self?.isTranscribing = false
            self?.isVoiceActionInFlight = false
        }
        transcriptionService.onStateChange = nil
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
