//
//  VoiceDictationCoordinator.swift
//  newsly
//

import Foundation

@MainActor
final class VoiceDictationCoordinator {
    private enum TaskKey: Hashable {
        case events
    }

    private let transcriber: any SpeechTranscribing
    private let tasks = TaskBag<TaskKey>()

    init(transcriber: any SpeechTranscribing) {
        self.transcriber = transcriber
    }

    deinit {
        tasks.cancelAll()
    }

    func listen(
        onTranscriptFinal: (@MainActor (String) async -> Void)? = nil,
        onError: (@MainActor (String) async -> Void)? = nil,
        onStateChange: (@MainActor (SpeechTranscriptionState) async -> Void)? = nil,
        onStopReason: (@MainActor (SpeechStopReason) async -> Void)? = nil
    ) {
        let events = transcriber.events()
        tasks.runReplacing(.events) {
            for await event in events {
                switch event {
                case .transcriptDelta:
                    continue
                case .transcriptFinal(let transcript):
                    await onTranscriptFinal?(transcript)
                case .error(let message):
                    await onError?(message)
                case .stateChange(let state):
                    await onStateChange?(state)
                case .stopReason(let reason):
                    await onStopReason?(reason)
                }
            }
        }
    }

    func stopListening() {
        tasks.cancel(.events)
    }
}
