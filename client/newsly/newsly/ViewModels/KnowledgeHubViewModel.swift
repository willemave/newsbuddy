//
//  KnowledgeHubViewModel.swift
//  newsly
//

import Foundation
import SwiftUI

@MainActor
protocol KnowledgeHubChatServicing: AnyObject {
    func listSessions(contentId: Int?, limit: Int) async throws -> [ChatSessionSummary]

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse
}

extension ChatService: KnowledgeHubChatServicing {}

@MainActor
class KnowledgeHubViewModel: ObservableObject {
    @Published var recentSessions: [ChatSessionSummary] = []
    @Published var isLoading = false
    @Published var isCreatingSession = false
    @Published var errorMessage: String?

    private let chatService: any KnowledgeHubChatServicing

    private let hubContext = AssistantScreenContext(
        screenType: "knowledge_hub",
        screenTitle: "Knowledge"
    )

    init(chatService: any KnowledgeHubChatServicing = ChatService.shared) {
        self.chatService = chatService
    }

    func loadHub() async {
        isLoading = true
        errorMessage = nil

        do {
            let sessions = try await chatService.listSessions(contentId: nil, limit: 10)
            recentSessions = sessions
                .filter { $0.sessionType != "voice_live" && !$0.isLiveVoiceSession }
                .prefix(5)
                .map { $0 }
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func startSearchChat(message: String) async -> ChatSessionRoute? {
        await startHubAssistantTurn(message: message)
    }

    func startSummaryChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: "Give me a summary of the last day's content. What are the key themes and most important takeaways?"
        )
    }

    func startCommentsChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: "What are the most interesting and insightful comments from the content I've received recently? Highlight any surprising perspectives or debates."
        )
    }

    func startFindArticlesChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: "Find a few new articles or sources I should read next based on what I've been reading."
        )
    }

    func startFindFeedsChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: "Recommend a few feeds, newsletters, or podcasts I should add based on what I've been reading."
        )
    }

    private func startHubAssistantTurn(message: String) async -> ChatSessionRoute? {
        guard !isCreatingSession else { return nil }
        isCreatingSession = true
        errorMessage = nil
        defer { isCreatingSession = false }

        do {
            let response = try await chatService.createAssistantTurn(
                message: message,
                sessionId: nil,
                screenContext: hubContext
            )
            return ChatSessionRoute(sessionId: response.session.id)
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }
}
