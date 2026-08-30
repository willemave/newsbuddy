//
//  ChatDependencies.swift
//  newsly
//

import Foundation

struct ChatDependencies {
    var chatService: any ChatSessionServicing
    var messageCompletionRegistry: ChatMessageCompletionRegistry
    var transcriptionService: any SpeechTranscribing
    var activeSessionManager: ActiveChatSessionManager
    var refreshTranscriptionAvailability: () async -> Bool
    var setBackendTranscriptionAvailable: (Bool) -> Void

    @MainActor
    static var live: ChatDependencies {
        live(activeSessionManager: .shared)
    }

    @MainActor
    static func live(activeSessionManager: ActiveChatSessionManager) -> ChatDependencies {
        return ChatDependencies(
            chatService: ChatService.shared,
            messageCompletionRegistry: activeSessionManager.messageCompletionRegistry,
            transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            activeSessionManager: activeSessionManager,
            refreshTranscriptionAvailability: {
                await OpenAIService.shared.refreshTranscriptionAvailability()
            },
            setBackendTranscriptionAvailable: { isAvailable in
                AppSettings.shared.setBackendTranscriptionAvailable(isAvailable)
            }
        )
    }
}
