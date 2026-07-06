import XCTest
@testable import newsly

@MainActor
final class LearningDeckReaderViewModelTests: XCTestCase {
    func testHandleDisappearKeepsPreAckSendAliveAndResumesPollingAfterAppear() async {
        let ackGate = AsyncGate()
        let chatService = MockLearningDeckReaderChatService(
            createAssistantTurnHandler: { message, _, _ in
                await ackGate.wait()
                return Self.assistantTurnResponse(message: message)
            },
            messageStatusHandler: { messageId in
                MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: Self.message(
                        id: 201,
                        role: .assistant,
                        content: "Assistant reply",
                        status: .completed
                    ),
                    error: nil
                )
            }
        )
        let viewModel = LearningDeckReaderViewModel(
            deck: Self.deck(),
            chatService: chatService,
            deckService: .shared
        )

        viewModel.performSendMessage(text: "Hello deck")
        let didStartSend = await waitUntil {
            chatService.createdTurns.map(\.message) == ["Hello deck"] && viewModel.isSending
        }

        XCTAssertTrue(didStartSend)
        viewModel.handleDisappear()
        XCTAssertTrue(viewModel.isSending)

        await ackGate.open()
        let didSuspendAfterAck = await waitUntil {
            viewModel.session?.id == 42 && !viewModel.isSending
        }

        XCTAssertTrue(didSuspendAfterAck)
        XCTAssertEqual(chatService.messageStatusCallCount, 0)
        XCTAssertEqual(viewModel.timeline.first?.pendingMessageId, 501)
        XCTAssertEqual(viewModel.timeline.first?.message.content, "Hello deck")

        viewModel.handleAppear()
        let didResumeAndComplete = await waitUntil {
            viewModel.timeline.contains { $0.message.content == "Assistant reply" }
                && !viewModel.isSending
        }

        XCTAssertTrue(didResumeAndComplete)
        XCTAssertEqual(chatService.messageStatusCallCount, 1)
        XCTAssertNil(viewModel.timeline.first { $0.message.isUser }?.pendingMessageId)
    }

    func testHandleDisappearCancelsAcceptedPollingWithoutFailingMessage() async {
        let chatService = MockLearningDeckReaderChatService(
            createAssistantTurnHandler: { message, _, _ in
                Self.assistantTurnResponse(message: message)
            },
            messageStatusHandler: { _ in
                while true {
                    try Task.checkCancellation()
                    try await Task.sleep(nanoseconds: 10_000_000)
                }
            }
        )
        let viewModel = LearningDeckReaderViewModel(
            deck: Self.deck(),
            chatService: chatService,
            deckService: .shared
        )

        viewModel.performSendMessage(text: "Keep this")
        let didStartPolling = await waitUntil {
            chatService.messageStatusCallCount > 0 && viewModel.isSending
        }

        XCTAssertTrue(didStartPolling)
        viewModel.handleDisappear()
        let didCancelPolling = await waitUntil { !viewModel.isSending }

        XCTAssertTrue(didCancelPolling)
        let userItem = viewModel.timeline.first { $0.message.isUser }
        XCTAssertEqual(userItem?.message.content, "Keep this")
        XCTAssertEqual(userItem?.pendingMessageId, 501)
        XCTAssertFalse(userItem?.message.hasFailed ?? true)
    }

    private static func deck() -> LearningDeck {
        LearningDeck(
            id: 1,
            title: "Deck",
            sourceKind: .content,
            sourceURL: nil,
            sourceContentId: 7,
            sourceTitle: "Source",
            sourceMetadata: [:],
            status: .completed,
            shareEnabled: false,
            viewerAvailable: true,
            sourceNotesAvailable: false,
            latestSuccessfulRunId: nil,
            latestRun: nil,
            createdAt: ServerDate.parse("2026-04-01T10:00:00Z")!,
            updatedAt: nil
        )
    }

    private static func assistantTurnResponse(message: String) -> AssistantTurnResponse {
        AssistantTurnResponse(
            session: session(),
            userMessage: Self.message(
                id: 101,
                role: .user,
                content: message,
                status: .processing
            ),
            messageId: 501,
            status: .processing
        )
    }

    private static func message(
        id: Int,
        role: APIChatMessageRole,
        content: String,
        status: APIMessageProcessingStatus
    ) -> ChatMessage {
        ChatMessage(
            id: id,
            role: role,
            timestamp: ServerDate.parse("2026-04-01T10:00:00Z")!,
            content: content,
            status: status
        )
    }

    private static func session() -> ChatSessionSummary {
        ChatSessionSummary(
            id: 42,
            contentId: 7,
            title: "Deck Chat",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.5",
            createdAt: ServerDate.parse("2026-04-01T10:00:00Z")!,
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: "Deck",
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: true,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }

    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<100 {
            if condition() {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return condition()
    }
}

private final class MockLearningDeckReaderChatService: LearningDeckReaderChatServicing {
    private let createAssistantTurnHandler: (
        String,
        Int?,
        AssistantScreenContext
    ) async throws -> AssistantTurnResponse
    private let messageStatusHandler: (Int) async throws -> MessageStatusResponse

    private(set) var createdTurns: [(message: String, sessionId: Int?)] = []
    private(set) var messageStatusCallCount = 0

    init(
        createAssistantTurnHandler: @escaping (
            String,
            Int?,
            AssistantScreenContext
        ) async throws -> AssistantTurnResponse,
        messageStatusHandler: @escaping (Int) async throws -> MessageStatusResponse
    ) {
        self.createAssistantTurnHandler = createAssistantTurnHandler
        self.messageStatusHandler = messageStatusHandler
    }

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        createdTurns.append((message: message, sessionId: sessionId))
        return try await createAssistantTurnHandler(message, sessionId, screenContext)
    }

    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        messageStatusCallCount += 1
        return try await messageStatusHandler(messageId)
    }
}

private actor AsyncGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if isOpen {
            return
        }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func open() {
        isOpen = true
        let continuations = waiters
        waiters.removeAll()
        for continuation in continuations {
            continuation.resume()
        }
    }
}
