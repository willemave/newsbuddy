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

}
