import Foundation
import XCTest
@testable import newsly

@MainActor
final class LearningHubViewModelTests: XCTestCase {
    func testVoiceMicStopsRecordingAndStartsAssistantTurnWithTranscript() async {
        let transcriptionService = MockLearningSpeechTranscriber(transcript: "What should I read next?")
        let chatService = MockLearningHubChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 91))]
        )
        let viewModel = LearningHubViewModel(
            chatService: chatService,
            transcriptionService: transcriptionService,
            initialVoiceDictationAvailable: true
        )

        let startRoute = await viewModel.toggleVoiceRecording()
        XCTAssertNil(startRoute)
        XCTAssertTrue(viewModel.isVoiceRecording)

        let route = await viewModel.toggleVoiceRecording()

        XCTAssertEqual(route?.sessionId, 91)
        XCTAssertEqual(chatService.receivedMessages, ["What should I read next?"])
        XCTAssertEqual(chatService.receivedScreenTypes, ["learning"])
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 1)
        XCTAssertFalse(viewModel.isVoiceRecording)
        XCTAssertFalse(viewModel.isVoiceTranscribing)

        viewModel.cancelVoiceRecording()
        XCTAssertEqual(transcriptionService.resetCallCount, 1)
    }

    func testVoiceSilenceAutoStopPublishesCompletedRoute() async {
        let transcriptionService = MockLearningSpeechTranscriber(transcript: "Summarize my unread stories")
        let chatService = MockLearningHubChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 92))]
        )
        let viewModel = LearningHubViewModel(
            chatService: chatService,
            transcriptionService: transcriptionService,
            initialVoiceDictationAvailable: true
        )

        _ = await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateSilenceAutoStop()
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(viewModel.completedVoiceRoute?.sessionId, 92)
        XCTAssertEqual(chatService.receivedMessages, ["Summarize my unread stories"])
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
        XCTAssertFalse(viewModel.isVoiceRecording)
        XCTAssertFalse(viewModel.isVoiceTranscribing)
    }

    func testCancelVoiceRecordingResetsTranscriberAndState() async {
        let transcriptionService = MockLearningSpeechTranscriber(transcript: "Ignore this")
        let viewModel = LearningHubViewModel(
            chatService: MockLearningHubChatService(turnResponses: []),
            transcriptionService: transcriptionService,
            initialVoiceDictationAvailable: true
        )

        _ = await viewModel.toggleVoiceRecording()
        viewModel.cancelVoiceRecording()

        XCTAssertEqual(transcriptionService.resetCallCount, 1)
        XCTAssertFalse(viewModel.isVoiceRecording)
        XCTAssertFalse(viewModel.isVoiceTranscribing)
        XCTAssertFalse(viewModel.isVoiceActionInFlight)
    }

    func testStartChatCreatesAssistantTurnWithLearningContext() async {
        let chatService = MockLearningHubChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 91))]
        )
        let viewModel = LearningHubViewModel(chatService: chatService)

        let route = await viewModel.startChat(message: "What changed this week?")

        XCTAssertEqual(route?.sessionId, 91)
        XCTAssertEqual(route?.initialUserMessageText, "Prompt")
        XCTAssertEqual(route?.initialUserMessageTimestamp, ServerDate.parse("2026-03-21T18:00:00Z"))
        XCTAssertEqual(route?.pendingMessageId, 291)
        XCTAssertEqual(chatService.receivedMessages, ["What changed this week?"])
        XCTAssertEqual(chatService.receivedSessionIds, [nil])
        XCTAssertEqual(chatService.receivedScreenTypes, ["learning"])
        XCTAssertEqual(chatService.receivedScreenTitles, ["Learning"])
        XCTAssertEqual(chatService.receivedQueries, [nil])
        XCTAssertEqual(chatService.receivedNotes, [nil])
        XCTAssertEqual(chatService.receivedAssistantActions, [nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [91])
    }

    func testLoadLearningStoresFirstHistoryPageAndPagination() async {
        let sessions = [
            makeSession(id: 1),
            makeSession(id: 2),
            makeSession(id: 3),
        ]
        let chatService = MockLearningHubChatService(
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
        let viewModel = LearningHubViewModel(chatService: chatService)

        await viewModel.loadLearning()

        XCTAssertEqual(chatService.requestedPageLimits, [20])
        XCTAssertEqual(chatService.requestedPageCursors, [nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2, 3])
        XCTAssertTrue(viewModel.hasMoreSessions)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testLoadLearningIgnoresCancelledRefreshAndKeepsCurrentSessions() async {
        let chatService = MockLearningHubChatService(
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
        let viewModel = LearningHubViewModel(chatService: chatService)

        await viewModel.loadLearning()
        await viewModel.loadLearning()

        XCTAssertEqual(chatService.requestedPageCursors, [nil, nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2])
        XCTAssertTrue(viewModel.hasMoreSessions)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testLoadMoreAppendsUniqueSessions() async {
        let chatService = MockLearningHubChatService(
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
        let viewModel = LearningHubViewModel(chatService: chatService)

        await viewModel.loadLearning()
        await viewModel.loadMoreSessions()

        XCTAssertEqual(chatService.requestedPageCursors, [nil, "next-page"])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2, 3])
        XCTAssertFalse(viewModel.hasMoreSessions)
        XCTAssertFalse(viewModel.hasLoadMoreError)
    }

    func testStartChatStoresErrorWhenAssistantTurnFails() async {
        let chatService = MockLearningHubChatService(
            turnResponses: [.failure(MockLearningHubChatService.MockError.boom)]
        )
        let viewModel = LearningHubViewModel(chatService: chatService)

        let route = await viewModel.startChat(message: "Find me something new")

        XCTAssertNil(route)
        XCTAssertEqual(viewModel.errorMessage, "Boom")
    }

    private func makeAssistantTurnResponse(sessionId: Int) -> AssistantTurnResponse {
        AssistantTurnResponse(
            session: makeSession(id: sessionId),
            userMessage: ChatMessage(
                id: 100 + sessionId,
                role: .user,
                timestamp: ServerDate.parse("2026-03-21T18:00:00Z")!,
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
            createdAt: ServerDate.parse("2026-03-21T18:00:00Z")!,
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
private final class MockLearningSpeechTranscriber: SpeechTranscribing {
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptFinal: ((String) -> Void)?
    var onError: ((String) -> Void)?
    var onStateChange: ((SpeechTranscriptionState) -> Void)?
    var onStopReason: ((SpeechStopReason) -> Void)?

    var isAvailable = true
    var isRecording = false
    var isTranscribing = false
    private(set) var startCallCount = 0
    private(set) var stopCallCount = 0
    private(set) var resetCallCount = 0

    private let transcript: String

    init(transcript: String) {
        self.transcript = transcript
    }

    func start() async throws {
        startCallCount += 1
        isRecording = true
        isTranscribing = false
        onStateChange?(.recording)
    }

    func stop() async throws -> String {
        stopCallCount += 1
        isRecording = false
        isTranscribing = true
        onStateChange?(.transcribing)
        onTranscriptFinal?(transcript)
        isTranscribing = false
        onStopReason?(.manual)
        onStateChange?(.idle)
        return transcript
    }

    func simulateSilenceAutoStop() async {
        isRecording = false
        isTranscribing = true
        onStateChange?(.transcribing)
        onTranscriptFinal?(transcript)
        isTranscribing = false
        onStopReason?(.silenceAutoStop)
        onStateChange?(.idle)
    }

    func cancel() {
        reset()
        onStopReason?(.cancel)
    }

    func reset() {
        resetCallCount += 1
        isRecording = false
        isTranscribing = false
        onStateChange?(.idle)
    }
}

@MainActor
private final class MockLearningHubChatService: LearningHubChatServicing {
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

}
