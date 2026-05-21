import Foundation
import XCTest
@testable import newsly

@MainActor
final class KnowledgeHubViewModelTests: XCTestCase {
    func testStartSearchChatCreatesAssistantTurnWithKnowledgeContext() async {
        let chatService = MockKnowledgeHubChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 91))]
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        let route = await viewModel.startSearchChat(message: "What changed this week?")

        XCTAssertEqual(route?.sessionId, 91)
        XCTAssertEqual(route?.initialUserMessageText, "Prompt")
        XCTAssertEqual(route?.initialUserMessageTimestamp, "2026-03-21T18:00:00Z")
        XCTAssertEqual(route?.pendingMessageId, 291)
        XCTAssertEqual(chatService.receivedMessages, ["What changed this week?"])
        XCTAssertEqual(chatService.receivedSessionIds, [nil])
        XCTAssertEqual(chatService.receivedScreenTypes, ["knowledge_hub"])
        XCTAssertEqual(chatService.receivedScreenTitles, ["Knowledge"])
        XCTAssertEqual(chatService.receivedQueries, [nil])
        XCTAssertEqual(chatService.receivedNotes, [nil])
        XCTAssertEqual(chatService.receivedAssistantActions, [nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [91])
    }

    func testSeededActionsUseExpectedPrompts() async {
        let chatService = MockKnowledgeHubChatService(
            turnResponses: [
                .success(makeAssistantTurnResponse(sessionId: 10)),
                .success(makeAssistantTurnResponse(sessionId: 11)),
                .success(makeAssistantTurnResponse(sessionId: 12)),
                .success(makeAssistantTurnResponse(sessionId: 13)),
            ]
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        _ = await viewModel.startSummaryChat()
        _ = await viewModel.startCommentsChat()
        _ = await viewModel.startFindArticlesChat()
        _ = await viewModel.startFindFeedsChat()

        XCTAssertEqual(
            chatService.receivedMessages,
            [
                "Give me a summary of the last day's content from my feed, including recent news items and articles. What are the key themes and most important takeaways?",
                "What are the most interesting and insightful comments from the news items and articles in my feed recently? Highlight any surprising perspectives or debates.",
                "Find a few new articles or sources I should read next based on what I've been reading.",
                "Recommend a few feeds, newsletters, or podcasts I should add based on what I've been reading.",
            ]
        )
        XCTAssertEqual(
            chatService.receivedQueries,
            [
                "recent news items and articles from my feed",
                nil,
                nil,
                nil,
            ]
        )
        XCTAssertEqual(
            chatService.receivedNotes,
            [
                "Summarize recent in-app feed content. Include both short-form news items and longer articles. Prefer in-app content before web search.",
                nil,
                nil,
                nil,
            ]
        )
        XCTAssertEqual(chatService.receivedAssistantActions, [nil, nil, nil, nil])
    }

    func testInterestingUnreadNewsActionUsesAssistantActionIntent() async {
        let chatService = MockKnowledgeHubChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 14))]
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        _ = await viewModel.startInterestingUnreadNewsChat()

        XCTAssertEqual(
            chatService.receivedMessages,
            [
                "Look at my unread fast-news items and pick the most interesting stories I should pay attention to."
            ]
        )
        XCTAssertEqual(chatService.receivedQueries, ["most interesting unread fast-news items"])
        XCTAssertEqual(
            chatService.receivedNotes,
            [
                "Use the unread fast-news tool result as the candidate set. Do not rely on currently visible rows only."
            ]
        )
        XCTAssertEqual(
            chatService.receivedAssistantActions,
            [AssistantActionIntent.pickInterestingUnreadNews]
        )
    }

    func testLoadHubStoresFirstHistoryPageAndPagination() async {
        let sessions = [
            makeSession(id: 1),
            makeSession(id: 2),
            makeSession(id: 3),
        ]
        let chatService = MockKnowledgeHubChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: sessions,
                        nextCursor: "next-page",
                        hasMore: true
                    )
                )
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        await viewModel.loadHub()

        XCTAssertEqual(chatService.requestedPageLimits, [20])
        XCTAssertEqual(chatService.requestedPageCursors, [nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2, 3])
        XCTAssertTrue(viewModel.hasMoreSessions)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testLoadHubIgnoresCancelledRefreshAndKeepsCurrentSessions() async {
        let chatService = MockKnowledgeHubChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1), makeSession(id: 2)],
                        nextCursor: "next-page",
                        hasMore: true
                    )
                ),
                .failure(APIError.networkError(URLError(.cancelled))),
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        await viewModel.loadHub()
        await viewModel.loadHub()

        XCTAssertEqual(chatService.requestedPageCursors, [nil, nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2])
        XCTAssertTrue(viewModel.hasMoreSessions)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testLoadMoreAppendsUniqueSessions() async {
        let chatService = MockKnowledgeHubChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1), makeSession(id: 2)],
                        nextCursor: "next-page",
                        hasMore: true
                    )
                ),
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 2), makeSession(id: 3)],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        await viewModel.loadHub()
        await viewModel.loadMoreSessions()

        XCTAssertEqual(chatService.requestedPageCursors, [nil, "next-page"])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2, 3])
        XCTAssertFalse(viewModel.hasMoreSessions)
        XCTAssertFalse(viewModel.hasLoadMoreError)
    }

    func testStartSearchChatStoresErrorWhenAssistantTurnFails() async {
        let chatService = MockKnowledgeHubChatService(
            turnResponses: [.failure(MockKnowledgeHubChatService.MockError.boom)]
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        let route = await viewModel.startSearchChat(message: "Find me something new")

        XCTAssertNil(route)
        XCTAssertEqual(viewModel.errorMessage, "Boom")
    }

    private func makeAssistantTurnResponse(sessionId: Int) -> AssistantTurnResponse {
        AssistantTurnResponse(
            session: makeSession(id: sessionId),
            userMessage: ChatMessage(
                id: 100 + sessionId,
                role: .user,
                timestamp: "2026-03-21T18:00:00Z",
                content: "Prompt",
                status: .processing
            ),
            messageId: 200 + sessionId,
            status: .processing
        )
    }

    private func makeSessionListResponse(
        sessions: [ChatSessionSummary],
        nextCursor: String?,
        hasMore: Bool
    ) -> ChatSessionListResponse {
        ChatSessionListResponse(
            sessions: sessions,
            meta: PaginationMetadata(
                nextCursor: nextCursor,
                hasMore: hasMore,
                pageSize: sessions.count,
                total: sessions.count
            )
        )
    }

    private func makeSession(id: Int, sessionType: String = "knowledge_chat") -> ChatSessionSummary {
        ChatSessionSummary(
            id: id,
            contentId: nil,
            title: "Session \(id)",
            sessionType: sessionType,
            topic: nil,
            llmProvider: "anthropic",
            llmModel: "anthropic:claude-sonnet-4-5",
            createdAt: "2026-03-21T18:00:00Z",
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }
}

@MainActor
private final class MockKnowledgeHubChatService: KnowledgeHubChatServicing {
    enum MockError: LocalizedError {
        case boom

        var errorDescription: String? {
            "Boom"
        }
    }

    var requestedPageLimits: [Int] = []
    var requestedPageCursors: [String?] = []
    var receivedMessages: [String] = []
    var receivedSessionIds: [Int?] = []
    var receivedScreenTypes: [String] = []
    var receivedScreenTitles: [String?] = []
    var receivedQueries: [String?] = []
    var receivedNotes: [String?] = []
    var receivedAssistantActions: [String?] = []

    private var pageResponses: [Result<ChatSessionListResponse, Error>]
    private var turnResponses: [Result<AssistantTurnResponse, Error>]

    init(
        pageResponses: [Result<ChatSessionListResponse, Error>] = [],
        turnResponses: [Result<AssistantTurnResponse, Error>]
    ) {
        self.pageResponses = pageResponses
        self.turnResponses = turnResponses
    }

    func listSessionsPage(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int,
        cursor: String?
    ) async throws -> ChatSessionListResponse {
        XCTAssertNil(contentId)
        XCTAssertNil(newsItemId)
        requestedPageLimits.append(limit)
        requestedPageCursors.append(cursor)

        guard !pageResponses.isEmpty else {
            XCTFail("Missing chat session page response")
            throw MockError.boom
        }

        return try pageResponses.removeFirst().get()
    }

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        receivedMessages.append(message)
        receivedSessionIds.append(sessionId)
        receivedScreenTypes.append(screenContext.screenType)
        receivedScreenTitles.append(screenContext.screenTitle)
        receivedQueries.append(screenContext.query)
        receivedNotes.append(screenContext.note)
        receivedAssistantActions.append(screenContext.assistantAction)

        guard !turnResponses.isEmpty else {
            XCTFail("Missing mock assistant turn response")
            throw MockError.boom
        }

        return try turnResponses.removeFirst().get()
    }

    func createSession(
        contentId: Int?,
        newsItemId: Int?,
        topic: String?,
        provider: ChatModelProvider?,
        modelHint: String?,
        initialMessage: String?
    ) async throws -> ChatSessionSummary {
        XCTAssertNil(contentId)
        XCTAssertNil(newsItemId)
        XCTAssertNil(topic)
        XCTAssertNil(provider)
        XCTAssertNil(modelHint)
        XCTAssertNil(initialMessage)
        return ChatSessionSummary(
            id: 999,
            contentId: nil,
            title: "New Session",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "gpt-5.4-mini",
            createdAt: "2026-03-21T18:00:00Z",
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isSavedToKnowledge: false,
            hasMessages: false,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }
}
