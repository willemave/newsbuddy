import Foundation
import XCTest
@testable import newsly

@MainActor
final class QuickMicViewModelTests: XCTestCase {
    func testEndHoldShowsOnlyLatestTurnPreview() async {
        let transcriptionService = MockSpeechTranscriber(
            transcripts: ["First question", "Second question"]
        )
        let chatService = MockQuickMicChatService(
            turns: [
                MockQuickMicChatService.Turn(
                    response: makeTurnResponse(
                        sessionId: 41,
                        userMessageId: 101,
                        messageId: 501,
                        content: "First question"
                    ),
                    assistantMessage: makeAssistantMessage(
                        id: 201,
                        content: "First answer"
                    )
                ),
                MockQuickMicChatService.Turn(
                    response: makeTurnResponse(
                        sessionId: 41,
                        userMessageId: 102,
                        messageId: 502,
                        content: "Second question"
                    ),
                    assistantMessage: makeAssistantMessage(
                        id: 202,
                        content: "Second answer"
                    )
                ),
            ]
        )
        let viewModel = QuickMicViewModel(
            transcriptionService: transcriptionService,
            chatService: chatService
        )
        let context = AssistantScreenContext(screenType: "fast_news")

        await viewModel.beginHold(screenContext: context)
        await viewModel.endHold()
        XCTAssertEqual(viewModel.messages.map(\.content), ["First question", "First answer"])
        XCTAssertEqual(viewModel.activeSession?.id, 41)

        await viewModel.beginHold(screenContext: context)
        await viewModel.endHold()

        XCTAssertEqual(viewModel.state, .modalActive)
        XCTAssertEqual(viewModel.messages.map(\.content), ["Second question", "Second answer"])
        XCTAssertEqual(chatService.receivedSessionIds, [nil, 41])
    }

    func testIgnoresTranscriptionErrorsAfterTurnSubmissionStarts() async {
        let transcriptionService = MockSpeechTranscriber(
            transcripts: ["What's the capital of Washington?"]
        )
        let assistantMessage = makeAssistantMessage(
            id: 301,
            content: "The capital of Washington is Olympia."
        )
        let chatService = MockQuickMicChatService(
            turns: [
                MockQuickMicChatService.Turn(
                    response: makeTurnResponse(
                        sessionId: 52,
                        userMessageId: 111,
                        messageId: 601,
                        content: "What's the capital of Washington?"
                    ),
                    assistantMessage: assistantMessage,
                    onWait: {
                        transcriptionService.onError?("Socket is not connected")
                    }
                )
            ]
        )
        let viewModel = QuickMicViewModel(
            transcriptionService: transcriptionService,
            chatService: chatService
        )

        await viewModel.beginHold(screenContext: AssistantScreenContext(screenType: "fast_news"))
        await viewModel.endHold()

        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.state, .modalActive)
        XCTAssertEqual(
            viewModel.messages.map(\.content),
            ["What's the capital of Washington?", "The capital of Washington is Olympia."]
        )
    }

    func testReRecordKeepsPanelVisibleForActiveSession() async {
        let transcriptionService = MockSpeechTranscriber(
            transcripts: ["First question", "Second question"]
        )
        let chatService = MockQuickMicChatService(
            turns: [
                MockQuickMicChatService.Turn(
                    response: makeTurnResponse(
                        sessionId: 77,
                        userMessageId: 121,
                        messageId: 701,
                        content: "First question"
                    ),
                    assistantMessage: makeAssistantMessage(
                        id: 221,
                        content: "First answer"
                    )
                ),
                MockQuickMicChatService.Turn(
                    response: makeTurnResponse(
                        sessionId: 77,
                        userMessageId: 122,
                        messageId: 702,
                        content: "Second question"
                    ),
                    assistantMessage: makeAssistantMessage(
                        id: 222,
                        content: "Second answer"
                    )
                ),
            ]
        )
        let viewModel = QuickMicViewModel(
            transcriptionService: transcriptionService,
            chatService: chatService
        )
        let context = AssistantScreenContext(screenType: "fast_news")

        await viewModel.beginHold(screenContext: context)
        await viewModel.endHold()

        XCTAssertEqual(viewModel.state, .modalActive)
        XCTAssertTrue(viewModel.isModalPresented)

        await viewModel.beginHold(screenContext: context)

        XCTAssertEqual(viewModel.state, .recordingWaveform)
        XCTAssertTrue(viewModel.isModalPresented)
        XCTAssertEqual(viewModel.activeSession?.id, 77)
        XCTAssertEqual(viewModel.messages.map(\.content), ["First question", "First answer"])
    }

    func testDismissPanelClearsQuickSessionState() async {
        let transcriptionService = MockSpeechTranscriber(
            transcripts: ["Can you recap this?"]
        )
        let chatService = MockQuickMicChatService(
            turns: [
                MockQuickMicChatService.Turn(
                    response: makeTurnResponse(
                        sessionId: 88,
                        userMessageId: 131,
                        messageId: 801,
                        content: "Can you recap this?"
                    ),
                    assistantMessage: makeAssistantMessage(
                        id: 231,
                        content: "Here is the recap."
                    )
                )
            ]
        )
        let viewModel = QuickMicViewModel(
            transcriptionService: transcriptionService,
            chatService: chatService
        )

        await viewModel.beginHold(screenContext: AssistantScreenContext(screenType: "fast_news"))
        await viewModel.endHold()

        XCTAssertTrue(viewModel.isModalPresented)
        XCTAssertEqual(viewModel.activeSession?.id, 88)

        viewModel.dismissPanel()

        XCTAssertEqual(viewModel.state, .idle)
        XCTAssertFalse(viewModel.isModalPresented)
        XCTAssertNil(viewModel.activeSession)
        XCTAssertTrue(viewModel.messages.isEmpty)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.activeTranscript, "")
    }

    private func makeTurnResponse(
        sessionId: Int,
        userMessageId: Int,
        messageId: Int,
        content: String
    ) -> AssistantTurnResponse {
        AssistantTurnResponse(
            session: ChatSessionSummary(
                id: sessionId,
                contentId: nil,
                title: "Quick Assistant",
                sessionType: "assistant_quick",
                topic: nil,
                llmProvider: "anthropic",
                llmModel: "anthropic:claude-sonnet-4-5",
                createdAt: "2026-03-13T19:00:00Z",
                updatedAt: nil,
                lastMessageAt: nil,
                articleTitle: nil,
                articleUrl: nil,
                articleSummary: nil,
                articleSource: nil,
                hasPendingMessage: true,
                isFavorite: false,
                hasMessages: true,
                lastMessagePreview: nil,
                lastMessageRole: nil
            ),
            userMessage: ChatMessage(
                id: userMessageId,
                role: .user,
                timestamp: "2026-03-13T19:00:00Z",
                content: content,
                status: .processing
            ),
            messageId: messageId,
            status: .processing
        )
    }

    private func makeAssistantMessage(id: Int, content: String) -> ChatMessage {
        ChatMessage(
            id: id,
            role: .assistant,
            timestamp: "2026-03-13T19:00:02Z",
            content: content,
            status: .completed
        )
    }
}

@MainActor
private final class MockSpeechTranscriber: SpeechTranscribing {
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptFinal: ((String) -> Void)?
    var onError: ((String) -> Void)?
    var onStateChange: ((SpeechTranscriptionState) -> Void)?
    var onStopReason: ((SpeechStopReason) -> Void)?

    var isAvailable = true
    var isRecording = false
    var isTranscribing = false

    private var transcripts: [String]

    init(transcripts: [String]) {
        self.transcripts = transcripts
    }

    func start() async throws {
        isRecording = true
        isTranscribing = true
        onStateChange?(.recording)
    }

    func stop() async throws -> String {
        isRecording = false
        isTranscribing = false
        onStateChange?(.idle)
        let transcript = transcripts.removeFirst()
        onTranscriptFinal?(transcript)
        return transcript
    }

    func cancel() {
        reset()
        onStopReason?(.cancel)
    }

    func reset() {
        isRecording = false
        isTranscribing = false
        onStateChange?(.idle)
    }
}

@MainActor
private final class MockQuickMicChatService: QuickMicChatServicing {
    struct Turn {
        let response: AssistantTurnResponse
        let assistantMessage: ChatMessage
        var onWait: (() -> Void)? = nil
    }

    var turns: [Turn]
    var receivedSessionIds: [Int?] = []
    private var pendingAssistantMessages: [Int: ChatMessage] = [:]
    private var waitHooks: [Int: () -> Void] = [:]

    init(turns: [Turn]) {
        self.turns = turns
    }

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        receivedSessionIds.append(sessionId)
        let turn = turns.removeFirst()
        pendingAssistantMessages[turn.response.messageId] = turn.assistantMessage
        if let onWait = turn.onWait {
            waitHooks[turn.response.messageId] = onWait
        }
        return turn.response
    }

    func waitForMessageCompletion(messageId: Int) async throws -> ChatMessage {
        waitHooks[messageId]?()
        return pendingAssistantMessages[messageId]!
    }
}
