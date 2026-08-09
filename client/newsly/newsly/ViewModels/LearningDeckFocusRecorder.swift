import Foundation
import Observation

@MainActor
@Observable
final class LearningDeckFocusRecorder {
    private(set) var isVoiceActionInFlight = false
    private(set) var state: SpeechTranscriptionState = .idle
    var errorMessage: String?

    var isRecording: Bool { state == .recording }
    var isTranscribing: Bool { state == .transcribing }

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

    init(
        transcriptionService: (any SpeechTranscribing)? = nil,
        refreshTranscriptionAvailability: @escaping () async -> Bool
    ) {
        let service = transcriptionService
            ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: service)
        self.refreshTranscriptionAvailability = refreshTranscriptionAvailability
    }

    deinit {
        MainActor.assumeIsolated {
            voiceCoordinator.cancel()
        }
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
        guard voiceCoordinator.hasActiveSession || isRecording || isTranscribing || pendingTranscript != nil else {
            return
        }
        voiceCoordinator.cancel()
        pendingTranscript = nil
        transcriptHandler = nil
        apply(.idle)
        isVoiceActionInFlight = false
    }

    private func startRecording() async {
        if !voiceDictationAvailable {
            await checkAndRefreshVoiceDictation()
        }
        guard voiceDictationAvailable else {
            applyFailure("Microphone is unavailable right now. Try again in a moment.")
            return
        }

        pendingTranscript = nil
        errorMessage = nil
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            try await voiceCoordinator.start(
                onTranscriptFinal: { [weak self] transcript in
                    self?.pendingTranscript = transcript
                },
                onError: { [weak self] message in
                    self?.applyFailure(message)
                },
                onStateChange: { [weak self] state in
                    self?.apply(state)
                },
                onStopReason: { [weak self] reason in
                    self?.handleStopReason(reason)
                }
            )
        } catch {
            applyFailure(error.localizedDescription)
        }
    }

    private func stopRecording() async {
        guard isRecording else { return }
        isVoiceActionInFlight = true
        apply(.transcribing)
        defer { isVoiceActionInFlight = false }

        do {
            let transcript = try await voiceCoordinator.stop()
            pendingTranscript = nil
            apply(.idle)
            applyTranscript(transcript)
        } catch {
            applyFailure(error.localizedDescription)
        }
    }

    private func checkAndRefreshVoiceDictation() async {
        if voiceCoordinator.isAvailable {
            voiceDictationAvailable = true
            return
        }
        voiceDictationAvailable = await refreshTranscriptionAvailability()
    }

    private func apply(_ state: SpeechTranscriptionState) {
        if case .failed(let message) = state {
            applyFailure(message)
        } else {
            self.state = state
        }
    }

    private func applyFailure(_ message: String) {
        state = .failed(message)
        errorMessage = message
        pendingTranscript = nil
        isVoiceActionInFlight = false
    }

    private func handleStopReason(_ reason: SpeechStopReason) {
        switch reason {
        case .manual:
            return
        case .silenceAutoStop, .maximumDuration:
            let transcript = pendingTranscript ?? ""
            pendingTranscript = nil
            apply(.idle)
            isVoiceActionInFlight = false
            applyTranscript(transcript)
        case .noSpeechTimeout:
            applyFailure("No speech detected. Try again.")
        case .cancel:
            pendingTranscript = nil
            apply(.idle)
            isVoiceActionInFlight = false
        case .failure:
            isVoiceActionInFlight = false
        }
    }

    private func applyTranscript(_ transcript: String) {
        let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTranscript.isEmpty else {
            applyFailure("I didn't catch that. Try again.")
            return
        }

        errorMessage = nil
        transcriptHandler?(trimmedTranscript)
    }
}
