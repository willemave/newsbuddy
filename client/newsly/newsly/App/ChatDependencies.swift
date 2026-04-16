//
//  ChatDependencies.swift
//  newsly
//

import Foundation

struct ChatDependencies {
    var chatService: any ChatSessionServicing
    var transcriptionService: any SpeechTranscribing
    var activeSessionManager: ActiveChatSessionManager

    @MainActor
    static var live: ChatDependencies {
        ChatDependencies(
            chatService: ChatService.shared,
            transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            activeSessionManager: ActiveChatSessionManager.shared
        )
    }
}
