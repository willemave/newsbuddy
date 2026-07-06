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
    func startDeepResearch(contentId: Int?, newsItemId: Int?, topic: String?) async throws -> ChatSessionSummary
    func sendMessageAsync(sessionId: Int, message: String) async throws -> SendChatMessageResponse
}

@MainActor
protocol ChatRouteOpening: AnyObject {
    func open(_ route: ChatSessionRoute)
    func openAssistantTurn(_ response: AssistantTurnResponse)
}

extension ChatService: DetailChatServicing {}
extension ChatNavigationCoordinator: ChatRouteOpening {}

@MainActor
@Observable
final class DetailChatCoordinator {
    private(set) var isCheckingChatSession = false
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

    func prepareChatSheetPresentation() -> Bool {
        guard !isCheckingChatSession else { return false }
        isCheckingChatSession = true
        defer { isCheckingChatSession = false }
        chatError = nil
        return true
    }

    func startChat(
        content: ContentDetail,
        visibleContentIds: [Int],
        provider: ChatModelProvider = .openai,
        prompt: String? = nil,
        onCloseSheet: () -> Void
    ) async {
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil
        defer { isStartingChat = false }

        do {
            if content.contentType == .news {
                if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    let response = try await chatService.createAssistantTurn(
                        message: prompt,
                        sessionId: nil,
                        screenContext: newsScreenContext(for: content, visibleContentIds: visibleContentIds)
                    )
                    onCloseSheet()
                    chatRouter.openAssistantTurn(response)
                } else {
                    let session = try await chatService.startNewsChat(
                        newsItemId: content.id,
                        provider: provider
                    )
                    onCloseSheet()
                    openChatSession(
                        sessionId: session.id,
                        content: content,
                        focusComposerOnAppear: true
                    )
                }
            } else {
                if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    let response = try await chatService.createAssistantTurn(
                        message: prompt,
                        sessionId: nil,
                        screenContext: articleScreenContext(for: content, visibleContentIds: visibleContentIds)
                    )
                    onCloseSheet()
                    chatRouter.openAssistantTurn(response)
                } else {
                    let session = try await chatService.startArticleChat(
                        contentId: content.id,
                        provider: provider
                    )
                    onCloseSheet()
                    openChatSession(
                        sessionId: session.id,
                        content: content,
                        focusComposerOnAppear: true
                    )
                }
            }
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            chatError = error.localizedDescription
        }
    }

    func startCouncil(
        content: ContentDetail,
        visibleContentIds _: [Int],
        provider: ChatModelProvider = .openai,
        onCloseSheet: () -> Void
    ) async {
        let prompt = councilPrompt(for: content)
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil
        defer { isStartingChat = false }

        do {
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
            onCloseSheet()
            openChatSession(
                sessionId: session.id,
                content: content,
                pendingCouncilPrompt: session.isCouncilMode ? nil : prompt
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            chatError = error.localizedDescription
        }
    }

    func startDeepDive(
        content: ContentDetail,
        visibleContentIds: [Int],
        onCloseSheet: () -> Void
    ) async {
        await startChat(
            content: content,
            visibleContentIds: visibleContentIds,
            provider: .openai,
            prompt: deepDivePrompt(for: content),
            onCloseSheet: onCloseSheet
        )
    }

    func startDeepResearch(
        content: ContentDetail,
        onCloseSheet: () -> Void
    ) async {
        let prompt = deepResearchPrompt(for: content)
        guard !isStartingChat else { return }

        isStartingChat = true
        chatError = nil
        defer { isStartingChat = false }

        do {
            let isNews = content.contentType == .news
            let session = try await chatService.startDeepResearch(
                contentId: isNews ? nil : content.id,
                newsItemId: isNews ? content.id : nil,
                topic: nil
            )
            let pendingResponse = try await chatService.sendMessageAsync(
                sessionId: session.id,
                message: prompt
            )

            onCloseSheet()
            openChatSession(
                sessionId: session.id,
                content: content,
                initialUserMessage: pendingResponse.userMessage,
                pendingMessageId: pendingResponse.messageId
            )
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            chatError = error.localizedDescription
        }
    }

    func handleReaderDigDeeper(
        selectedText: String,
        content: ContentDetail,
        visibleContentIds: [Int]
    ) {
        let trimmed = selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        Task { @MainActor in
            do {
                let isNews = content.contentType == .news
                let response = try await self.chatService.createAssistantTurn(
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
                self.chatRouter.openAssistantTurn(response)
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                self.toastPresenter.showError("Failed to dig deeper: \(error.localizedDescription)")
            }
        }
    }

    func openChatSession(
        sessionId: Int,
        content: ContentDetail,
        initialUserMessage: ChatMessage? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        focusComposerOnAppear: Bool = false
    ) {
        let isNews = content.contentType == .news
        openChatSession(
            sessionId: sessionId,
            contentId: isNews ? nil : content.id,
            newsItemId: isNews ? content.id : nil,
            initialUserMessage: initialUserMessage,
            pendingMessageId: pendingMessageId,
            pendingCouncilPrompt: pendingCouncilPrompt,
            focusComposerOnAppear: focusComposerOnAppear
        )
    }

    private func openChatSession(
        sessionId: Int,
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        initialUserMessage: ChatMessage? = nil,
        pendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        focusComposerOnAppear: Bool = false
    ) {
        chatSessionManager.stopTracking(sessionId: sessionId)
        chatRouter.open(
            ChatSessionRoute(
                sessionId: sessionId,
                contentId: contentId,
                newsItemId: newsItemId,
                initialUserMessageText: initialUserMessage?.content,
                initialUserMessageTimestamp: initialUserMessage?.timestamp,
                pendingMessageId: pendingMessageId,
                pendingCouncilPrompt: pendingCouncilPrompt,
                focusComposerOnAppear: focusComposerOnAppear
            )
        )
    }

    private func articleScreenContext(
        for content: ContentDetail,
        visibleContentIds: [Int]
    ) -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "content_detail",
            screenTitle: content.displayTitle,
            contentId: content.id,
            visibleContentIds: visibleContentIds,
            selectedTopic: content.displayTitle,
            note: "The user is viewing an article or podcast detail. Use the linked content and reader context as primary context."
        )
    }

    private func newsScreenContext(
        for content: ContentDetail,
        visibleContentIds: [Int]
    ) -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "content_detail",
            screenTitle: content.displayTitle,
            newsItemId: content.id,
            visibleNewsItemIds: visibleContentIds,
            selectedTopic: content.displayTitle,
            note: "The user is viewing a news item detail. Use the news item snapshot; do not resolve same-numbered content IDs."
        )
    }

    private func deepDivePrompt(for content: ContentDetail) -> String {
        "Dig deeper into the key points of \(content.displayTitle). For each main point, explain reasoning, supporting evidence, and include a bit more detail explaining the point. Also pull out key ideas from the discussion context when available, and add more insights from the discussion, including notable agreements and disagreements. Keep answers concise and numbered."
    }

    private func councilPrompt(for content: ContentDetail) -> String {
        "Give me your perspective on \(content.displayTitle). Keep it short: 2-4 concise bullets on what matters most, what is weak or missing, and what actions or implications follow."
    }

    private func deepResearchPrompt(for content: ContentDetail) -> String {
        "Conduct comprehensive research on \(content.displayTitle). Find additional sources, verify claims, identify related developments, and provide a thorough analysis with citations."
    }
}
