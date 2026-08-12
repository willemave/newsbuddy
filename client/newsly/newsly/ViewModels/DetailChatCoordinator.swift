//
//  DetailChatCoordinator.swift
//  newsly
//

import Foundation
import Observation

protocol DetailChatServicing: AnyObject {
    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse
    func startNewsChat(newsItemId: Int, provider: ChatModelProvider) async throws -> ChatSessionSummary
    func startArticleChat(contentId: Int, provider: ChatModelProvider) async throws -> ChatSessionSummary
    func startNewsTopicChat(newsItemId: Int, topic: String, provider: ChatModelProvider) async throws -> ChatSessionSummary
    func startTopicChat(contentId: Int, topic: String, provider: ChatModelProvider) async throws -> ChatSessionSummary
    func getSessionForNewsItem(newsItemId: Int) async throws -> ChatSessionSummary?
    func getSessionForContent(contentId: Int) async throws -> ChatSessionSummary?
}

@MainActor
protocol ChatRouteOpening: AnyObject {
    func open(_ route: ChatSessionRoute)
}

extension ChatService: DetailChatServicing {}
extension ChatNavigationCoordinator: ChatRouteOpening {}

@MainActor
@Observable
final class DetailChatCoordinator {
    private(set) var isStartingChat = false
    var chatError: String?

    private let chatSessionManager: ActiveChatSessionManager
    private let chatService: any DetailChatServicing
    private let chatRouter: any ChatRouteOpening
    private let toastPresenter: any ToastPresenting

    init(
        chatSessionManager: ActiveChatSessionManager,
        chatService: any DetailChatServicing,
        chatRouter: any ChatRouteOpening,
        toastPresenter: any ToastPresenting
    ) {
        self.chatSessionManager = chatSessionManager
        self.chatService = chatService
        self.chatRouter = chatRouter
        self.toastPresenter = toastPresenter
    }

    func activeSession(for content: ContentDetail) -> ActiveChatSession? {
        if content.contentType == .news {
            return chatSessionManager.getSession(forNewsItemId: content.id)
        }
        return chatSessionManager.getSession(forContentId: content.id)
    }

    func markSessionViewed(sessionId: Int) {
        chatSessionManager.markAsViewed(sessionId: sessionId)
    }

    func startTopicSession(
        content: ContentDetail,
        topic: String,
        provider: ChatModelProvider = .openai
    ) async throws -> ChatSessionSummary {
        if content.contentType == .news {
            return try await chatService.startNewsTopicChat(
                newsItemId: content.id,
                topic: topic,
                provider: provider
            )
        }
        return try await chatService.startTopicChat(
            contentId: content.id,
            topic: topic,
            provider: provider
        )
    }

    func startChat(
        content: ContentDetail,
        provider: ChatModelProvider = .openai
    ) async -> ChatSessionRoute? {
        await performChatStart {
            let session: ChatSessionSummary
            if content.contentType == .news {
                session = try await chatService.startNewsChat(
                    newsItemId: content.id,
                    provider: provider
                )
            } else {
                session = try await chatService.startArticleChat(
                    contentId: content.id,
                    provider: provider
                )
            }
            return chatRoute(
                sessionId: session.id,
                content: content,
                focusComposerOnAppear: true
            )
        }
    }

    func startCouncil(
        content: ContentDetail,
        provider: ChatModelProvider = .openai
    ) async -> ChatSessionRoute? {
        let prompt = councilPrompt(for: content)
        return await performChatStart {
            let session: ChatSessionSummary
            if content.contentType == .news {
                if let existingSession = try await chatService.getSessionForNewsItem(
                    newsItemId: content.id
                ) {
                    session = existingSession
                } else {
                    session = try await chatService.startNewsChat(
                        newsItemId: content.id,
                        provider: provider
                    )
                }
            } else {
                if let existingSession = try await chatService.getSessionForContent(
                    contentId: content.id
                ) {
                    session = existingSession
                } else {
                    session = try await chatService.startArticleChat(
                        contentId: content.id,
                        provider: provider
                    )
                }
            }
            return chatRoute(
                sessionId: session.id,
                content: content,
                pendingCouncilPrompt: session.isCouncilMode ? nil : prompt
            )
        }
    }

    func startReaderDigDeeper(
        selectedText: String,
        content: ContentDetail,
        visibleContentIds: [Int]
    ) async -> ChatSessionRoute? {
        let trimmed = selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }

        do {
            let isNews = content.contentType == .news
            let response = try await chatService.createAssistantTurn(
                message: "Dig deeper into this selected text from \(content.displayTitle): \"\(trimmed)\"",
                sessionId: nil,
                screenContext: AssistantScreenContext(
                    screenType: "article_reader",
                    screenTitle: "Article Reader",
                    contentId: isNews ? nil : content.id,
                    newsItemId: isNews ? content.id : nil,
                    visibleContentIds: isNews ? [] : visibleContentIds,
                    visibleNewsItemIds: isNews ? visibleContentIds : [],
                    selectedTopic: trimmed,
                    query: trimmed,
                    note: "The user selected text from the full article reader. Use the article body and selected passage as primary context. For news items, use news_item_id and do not resolve same-numbered content IDs."
                )
            )
            return ChatSessionRoute(assistantTurn: response)
        } catch where isNetworkCancellation(error) {
            return nil
        } catch {
            toastPresenter.showError("Failed to dig deeper: \(error.localizedDescription)")
            return nil
        }
    }

    func chatRoute(
        sessionId: Int,
        content: ContentDetail,
        initialUserMessage: ChatMessage? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        focusComposerOnAppear: Bool = false
    ) -> ChatSessionRoute {
        let isNews = content.contentType == .news
        return ChatSessionRoute(
            sessionId: sessionId,
            contentId: isNews ? nil : content.id,
            newsItemId: isNews ? content.id : nil,
            initialUserMessageText: initialUserMessage?.content,
            initialUserMessageTimestamp: initialUserMessage?.timestamp,
            pendingMessageId: pendingMessageId,
            pendingCouncilPrompt: pendingCouncilPrompt,
            focusComposerOnAppear: focusComposerOnAppear
        )
    }

    func open(_ route: ChatSessionRoute) {
        chatSessionManager.stopTracking(sessionId: route.sessionId)
        chatRouter.open(route)
    }

    private func performChatStart(
        _ operation: () async throws -> ChatSessionRoute
    ) async -> ChatSessionRoute? {
        guard !isStartingChat else { return nil }

        isStartingChat = true
        chatError = nil
        defer { isStartingChat = false }

        do {
            return try await operation()
        } catch where isNetworkCancellation(error) {
            return nil
        } catch {
            chatError = error.localizedDescription
            return nil
        }
    }

    private func councilPrompt(for content: ContentDetail) -> String {
        "Give me your perspective on \(content.displayTitle). Keep it short: 2-4 concise bullets on what matters most, what is weak or missing, and what actions or implications follow."
    }
}
