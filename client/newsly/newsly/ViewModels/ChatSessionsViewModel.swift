//
//  ChatSessionsViewModel.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation
import Observation
import SwiftUI

@MainActor
@Observable
final class ChatSessionsViewModel {
    var sessions: [ChatSessionSummary] = []
    var isLoading = false
    var errorMessage: String?

    @ObservationIgnored
    private let chatService: ChatService

    init(chatService: ChatService) {
        self.chatService = chatService
    }

    func loadSessions() async {
        isLoading = true
        errorMessage = nil

        do {
            sessions = try await chatService.listSessions(
                contentId: nil,
                newsItemId: nil,
                limit: 50
            )
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func deleteSessions(ids: [Int]) async {
        guard !ids.isEmpty else { return }

        errorMessage = nil
        let previousSessions = sessions
        sessions.removeAll { ids.contains($0.id) }

        do {
            for id in ids {
                try await chatService.deleteSession(sessionId: id)
            }
        } catch {
            sessions = previousSessions
            errorMessage = error.localizedDescription
            await loadSessions()
        }
    }
}
