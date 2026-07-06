//
//  ChatSessionsViewModel.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation
import Observation
import SwiftUI

protocol ChatSessionsServicing: AnyObject {
    func listSessions(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int
    ) async throws -> [ChatSessionSummary]

    func createSession(
        contentId: Int?,
        newsItemId: Int?,
        topic: String?,
        provider: ChatModelProvider?,
        modelHint: String?,
        initialMessage: String?
    ) async throws -> ChatSessionSummary

    func deleteSession(sessionId: Int) async throws
}

extension ChatService: ChatSessionsServicing {}

@MainActor
@Observable
final class ChatSessionsViewModel {
    var sessions: [ChatSessionSummary] = []
    var isLoading = false
    var errorMessage: String?

    @ObservationIgnored
    private let chatService: any ChatSessionsServicing

    init(chatService: any ChatSessionsServicing) {
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

    func createSession(
        contentId: Int? = nil,
        topic: String? = nil,
        provider: ChatModelProvider = .openai
    ) async -> ChatSessionSummary? {
        do {
            let session = try await chatService.createSession(
                contentId: contentId,
                newsItemId: nil,
                topic: topic,
                provider: provider,
                modelHint: nil,
                initialMessage: nil
            )
            // Prepend to list
            sessions.insert(session, at: 0)
            return session
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
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
