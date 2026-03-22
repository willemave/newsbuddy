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
        XCTAssertEqual(chatService.receivedMessages, ["What changed this week?"])
        XCTAssertEqual(chatService.receivedSessionIds, [nil])
        XCTAssertEqual(chatService.receivedScreenTypes, ["knowledge_hub"])
        XCTAssertEqual(chatService.receivedScreenTitles, ["Knowledge"])
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
                "Give me a summary of the last day's content. What are the key themes and most important takeaways?",
                "What are the most interesting and insightful comments from the content I've received recently? Highlight any surprising perspectives or debates.",
                "Find a few new articles or sources I should read next based on what I've been reading.",
                "Recommend a few feeds, newsletters, or podcasts I should add based on what I've been reading.",
            ]
        )
    }

    func testLoadHubFiltersVoiceSessionsAndLimitsToFive() async {
        let sessions = [
            makeSession(id: 1, sessionType: "voice_live"),
            makeSession(id: 2),
            makeSession(id: 3),
            makeSession(id: 4),
            makeSession(id: 5),
            makeSession(id: 6),
            makeSession(id: 7),
        ]
        let chatService = MockKnowledgeHubChatService(
            sessionsResult: .success(sessions),
            turnResponses: []
        )
        let viewModel = KnowledgeHubViewModel(chatService: chatService)

        await viewModel.loadHub()

        XCTAssertEqual(chatService.requestedListLimit, 10)
        XCTAssertEqual(viewModel.recentSessions.map(\.id), [2, 3, 4, 5, 6])
        XCTAssertNil(viewModel.errorMessage)
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
            isFavorite: false,
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

    var requestedListLimit: Int?
    var receivedMessages: [String] = []
    var receivedSessionIds: [Int?] = []
    var receivedScreenTypes: [String] = []
    var receivedScreenTitles: [String?] = []

    private let sessionsResult: Result<[ChatSessionSummary], Error>
    private var turnResponses: [Result<AssistantTurnResponse, Error>]

    init(
        sessionsResult: Result<[ChatSessionSummary], Error> = .success([]),
        turnResponses: [Result<AssistantTurnResponse, Error>]
    ) {
        self.sessionsResult = sessionsResult
        self.turnResponses = turnResponses
    }

    func listSessions(contentId: Int?, limit: Int) async throws -> [ChatSessionSummary] {
        XCTAssertNil(contentId)
        requestedListLimit = limit
        return try sessionsResult.get()
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

        guard !turnResponses.isEmpty else {
            XCTFail("Missing mock assistant turn response")
            throw MockError.boom
        }

        return try turnResponses.removeFirst().get()
    }
}
