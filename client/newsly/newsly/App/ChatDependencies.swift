//
//  ChatDependencies.swift
//  newsly
//

import Foundation

struct ChatDependencies {
    var chatService: any ChatSessionServicing
    var transcriptionService: any SpeechTranscribing
    var activeSessionManager: ActiveChatSessionManager
    var authService: any AuthenticationServicing
    var tokenStore: any AuthTokenStore
    var refreshTranscriptionAvailability: () async -> Bool
    var setBackendTranscriptionAvailable: (Bool) -> Void

    @MainActor
    static var live: ChatDependencies {
        ChatDependencies(
            chatService: ChatService.shared,
            transcriptionService: SpeechTranscriberFactory.makeVoiceDictationTranscriber(),
            activeSessionManager: ActiveChatSessionManager.shared,
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
