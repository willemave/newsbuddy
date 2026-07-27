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
    var authService: any AuthenticationServicing
    var tokenStore: any AuthTokenStore
    var refreshTranscriptionAvailability: () async -> Bool
    var setBackendTranscriptionAvailable: (Bool) -> Void

    @MainActor
    static var live: ChatDependencies {
        let activeSessionManager = ActiveChatSessionManager.shared
        return ChatDependencies(
            chatService: ChatService.shared,
            messageCompletionRegistry: activeSessionManager.messageCompletionRegistry,
            transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            activeSessionManager: activeSessionManager,
            authService: AuthenticationService.shared,
            tokenStore: KeychainManager.shared,
            refreshTranscriptionAvailability: {
                await OpenAIService.shared.refreshTranscriptionAvailability()
            },
            setBackendTranscriptionAvailable: { isAvailable in
                AppSettings.shared.setBackendTranscriptionAvailable(isAvailable)
            }
        )
    }
}
