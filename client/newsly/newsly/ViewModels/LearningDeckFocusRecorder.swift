import Foundation
import Observation

@MainActor
@Observable
final class LearningDeckFocusRecorder {
    var errorMessage: String?

    var isVoiceActionInFlight: Bool { voiceInput.isActionInFlight }
    var state: SpeechTranscriptionState { voiceInput.state }
    var isRecording: Bool { voiceInput.isRecording }
    var isTranscribing: Bool { voiceInput.isTranscribing }

    @ObservationIgnored
    private let voiceInput: ChatVoiceInputController

    init(
        transcriptionService: any SpeechTranscribing,
        refreshTranscriptionAvailability: @escaping () async -> Bool
    ) {
        self.voiceInput = ChatVoiceInputController(
            transcriptionService: transcriptionService,
            refreshAvailability: refreshTranscriptionAvailability,
            initiallyAvailable: false
        )
    }

    func toggleRecording(onTranscript: @escaping (String) -> Void) async {
        voiceInput.configure(
            onTranscriptReady: { transcript in
                onTranscript(transcript)
            },
            onError: { [weak self] message in
                self?.errorMessage = message
            }
        )
        errorMessage = nil
        await voiceInput.toggle()
    }

    func cancelRecording() {
        voiceInput.reset()
        errorMessage = nil
    }
}
