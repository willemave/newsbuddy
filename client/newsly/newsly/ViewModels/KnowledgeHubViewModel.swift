//
//  KnowledgeHubViewModel.swift
//  newsly
//

import Foundation
import SwiftUI

@MainActor
protocol KnowledgeHubChatServicing: AnyObject {
    func listSessionsPage(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int,
        cursor: String?
    ) async throws -> ChatSessionListResponse

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse

    func createSession(
        contentId: Int?,
        newsItemId: Int?,
        topic: String?,
        provider: ChatModelProvider?,
        modelHint: String?,
        initialMessage: String?
    ) async throws -> ChatSessionSummary
}

extension ChatService: KnowledgeHubChatServicing {}

@MainActor
class KnowledgeHubViewModel: ObservableObject {
    @Published var sessions: [ChatSessionSummary] = []
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var hasMoreSessions = false
    @Published var isCreatingSession = false
    @Published var errorMessage: String?
    @Published var hasLoadMoreError = false

    private let chatService: any KnowledgeHubChatServicing
    private var nextCursor: String?
    private let historyPageLimit = 20

    init(chatService: any KnowledgeHubChatServicing = ChatService.shared) {
        self.chatService = chatService
    }

    func loadHub() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }

        errorMessage = nil
        hasLoadMoreError = false

        do {
            let response = try await chatService.listSessionsPage(
                contentId: nil,
                newsItemId: nil,
                limit: historyPageLimit,
                cursor: nil
            )
            sessions = response.sessions
            nextCursor = response.meta.nextCursor
            hasMoreSessions = response.meta.hasMore
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            nextCursor = nil
            hasMoreSessions = false
            errorMessage = error.localizedDescription
        }
    }

    func loadMoreSessions() async {
        guard !isLoading, !isLoadingMore, hasMoreSessions, let cursor = nextCursor else {
            return
        }

        isLoadingMore = true
        hasLoadMoreError = false
        defer { isLoadingMore = false }

        do {
            let response = try await chatService.listSessionsPage(
                contentId: nil,
                newsItemId: nil,
                limit: historyPageLimit,
                cursor: cursor
            )
            appendUniqueSessions(response.sessions)
            nextCursor = response.meta.nextCursor
            hasMoreSessions = response.meta.hasMore
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            hasLoadMoreError = true
        }
    }

    func startNewChat() async -> ChatSessionRoute? {
        guard !isCreatingSession else { return nil }
        isCreatingSession = true
        errorMessage = nil
        defer { isCreatingSession = false }

        do {
            let session = try await chatService.createSession(
                contentId: nil,
                newsItemId: nil,
                topic: nil,
                provider: nil,
                modelHint: nil,
                initialMessage: nil
            )
            prependSession(session)
            return ChatSessionRoute(sessionId: session.id)
        } catch where isNetworkCancellation(error) {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    func startSearchChat(message: String) async -> ChatSessionRoute? {
        await startHubAssistantTurn(message: message)
    }

    func startSummaryChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: (
                "Give me a summary of the last day's content from my feed, "
                + "including recent news items and articles. "
                + "What are the key themes and most important takeaways?"
            ),
            screenContext: makeHubContext(
                query: "recent news items and articles from my feed",
                note: (
                    "Summarize recent in-app feed content. Include both short-form news "
                    + "items and longer articles. Prefer in-app content before web search."
                )
            )
        )
    }

    func startCommentsChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: (
                "What are the most interesting and insightful comments from the "
                + "news items and articles in my feed recently? "
                + "Highlight any surprising perspectives or debates."
            )
        )
    }

    func startInterestingUnreadNewsChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: InterestingUnreadNewsAssistantAction.prompt,
            screenContext: InterestingUnreadNewsAssistantAction.screenContext(
                screenType: "knowledge_hub",
                screenTitle: "Knowledge"
            )
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

    private func startHubAssistantTurn(
        message: String,
        screenContext: AssistantScreenContext? = nil
    ) async -> ChatSessionRoute? {
        guard !isCreatingSession else { return nil }
        isCreatingSession = true
        errorMessage = nil
        defer { isCreatingSession = false }

        do {
            let response = try await chatService.createAssistantTurn(
                message: message,
                sessionId: nil,
                screenContext: screenContext ?? makeHubContext()
            )
            prependSession(response.session)
            return ChatSessionRoute(
                sessionId: response.session.id,
                initialUserMessageText: response.userMessage.content,
                initialUserMessageTimestamp: response.userMessage.timestamp,
                pendingMessageId: response.messageId
            )
        } catch where isNetworkCancellation(error) {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    private func makeHubContext(
        query: String? = nil,
        note: String? = nil
    ) -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "knowledge_hub",
            screenTitle: "Knowledge",
            query: query,
            note: note
        )
    }

    private func appendUniqueSessions(_ newSessions: [ChatSessionSummary]) {
        var seenIds = Set(sessions.map(\.id))
        for session in newSessions where seenIds.insert(session.id).inserted {
            sessions.append(session)
        }
    }

    private func prependSession(_ session: ChatSessionSummary) {
        sessions.removeAll { $0.id == session.id }
        sessions.insert(session, at: 0)
    }
}
