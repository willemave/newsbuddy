import Foundation
import XCTest
@testable import newsly

@MainActor
final class ChatSessionViewModelTests: XCTestCase {
    func testDefaultChatDictationUsesRecordThenTranscribeService() {
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .live
        )
        let mirror = Mirror(reflecting: viewModel)
        let service = mirror.children.first { $0.label == "transcriptionService" }?.value as AnyObject?

        XCTAssertTrue(service === VoiceDictationService.shared)
    }

    func testToggleVoiceRecordingStartsRecordingOnFirstTap() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Ignored")
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()

        XCTAssertTrue(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
    }

    func testToggleVoiceRecordingStopsRecordingOnSecondTapAndPopulatesDraft() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(viewModel.inputText, "Final transcript")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 1)
    }

    func testToggleVoiceRecordingIgnoresTapWhileTranscribing() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Ignored")
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        viewModel.isTranscribing = true

        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(transcriptionService.startCallCount, 0)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertTrue(viewModel.isTranscribing)
    }

    func testStopVoiceRecordingPopulatesInputWithoutStreamingPreview() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        viewModel.isRecording = true
        XCTAssertEqual(viewModel.inputText, "")

        await viewModel.stopVoiceRecording()

        XCTAssertEqual(viewModel.inputText, "Final transcript")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertTrue(viewModel.allMessages.isEmpty)
    }

    func testStopVoiceRecordingAppendsToExistingDraft() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "second thought")
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        viewModel.inputText = "First draft"
        viewModel.isRecording = true

        await viewModel.stopVoiceRecording()

        XCTAssertEqual(viewModel.inputText, "First draft second thought")
    }

    func testSilenceAutoStopPopulatesDraftWithoutManualStop() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Auto transcript")
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(transcriptionService: transcriptionService),
            initialVoiceDictationAvailable: true
        )

        await viewModel.startVoiceRecording()
        await transcriptionService.simulateSilenceAutoStop()

        XCTAssertEqual(viewModel.inputText, "Auto transcript")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
    }

    func testCancelCouncilSelectionClearsInFlightState() async {
        let chatService = MockChatSessionService(selectCouncilBranchHandler: { _, _ in
            try await Task.sleep(nanoseconds: 60_000_000_000)
            throw CancellationError()
        })
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(session: Self.session(activeChildSessionId: 200)),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        let selectionTask = Task {
            await viewModel.selectCouncilBranch(childSessionId: 201)
        }
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(viewModel.selectingCouncilChildSessionId, 201)

        viewModel.cancelCouncilSelection()
        await selectionTask.value

        XCTAssertNil(viewModel.selectingCouncilChildSessionId)
        XCTAssertFalse(viewModel.councilSelectionTimedOut)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testRetryCouncilCandidateAppliesReturnedDetail() async {
        let retriedMessage = ChatMessage(
            id: 9,
            sourceMessageId: 9,
            role: .assistant,
            timestamp: "2026-04-01T10:00:00Z",
            content: "Ben Thompson regenerated.",
            councilCandidates: [
                CouncilCandidate(
                    personaId: "ben_thompson",
                    personaName: "Ben Thompson",
                    childSessionId: 201,
                    content: "Ben Thompson regenerated.",
                    status: "completed",
                    order: 0
                )
            ],
            activeCouncilChildSessionId: 201
        )
        let detail = ChatSessionDetail(
            session: Self.session(activeChildSessionId: 201),
            messages: [retriedMessage]
        )
        let chatService = MockChatSessionService(retryCouncilBranchHandler: { _, childSessionId in
            XCTAssertEqual(childSessionId, 201)
            return detail
        })
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(session: Self.session(activeChildSessionId: 200)),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        await viewModel.retryCouncilCandidate(childSessionId: 201)

        XCTAssertNil(viewModel.retryingCouncilChildSessionId)
        XCTAssertEqual(viewModel.activeCouncilChildSessionId, 201)
        XCTAssertEqual(viewModel.councilCandidates.first?.status, "completed")
        XCTAssertEqual(viewModel.councilCandidates.first?.content, "Ben Thompson regenerated.")
    }

    func testHandleDisappearCancelsOwnedSendTaskWithoutSurfacingError() async {
        let chatService = MockChatSessionService(sendMessageHandler: { _, _ in
            try await Task.sleep(nanoseconds: 60_000_000_000)
            throw CancellationError()
        })
        ActiveChatSessionManager.shared.reset()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.inputText = "Hello"
        viewModel.performSendMessage()
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertTrue(viewModel.isSending)
        XCTAssertEqual(viewModel.allMessages.last?.content, "Hello")

        viewModel.handleDisappear()
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertFalse(viewModel.isSending)
        XCTAssertFalse(viewModel.isStartingCouncil)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertTrue(viewModel.allMessages.isEmpty)
        ActiveChatSessionManager.shared.reset()
    }

    func testHandleDisappearHandsOffContentBackedProcessingMessageToBackgroundTracker() async {
        let session = Self.session(
            contentId: 7,
            articleTitle: "Tracked Article",
            hasPendingMessage: true
        )
        ActiveChatSessionManager.shared.reset()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(
                session: session,
                initialUserMessageText: "Track this",
                initialUserMessageTimestamp: "2026-04-01T10:00:00Z",
                pendingMessageId: 99
            ),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored")
            )
        )

        viewModel.handleDisappear()

        let tracked = ActiveChatSessionManager.shared.getSession(forContentId: 7)
        XCTAssertEqual(tracked?.id, 42)
        XCTAssertEqual(tracked?.messageId, 99)
        XCTAssertEqual(tracked?.contentTitle, "Tracked Article")
        ActiveChatSessionManager.shared.reset()
    }

    private static func session(
        contentId: Int? = nil,
        articleTitle: String? = nil,
        hasPendingMessage: Bool = false,
        activeChildSessionId: Int? = nil
    ) -> ChatSessionSummary {
        ChatSessionSummary(
            id: 42,
            contentId: contentId,
            title: "Chat",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.4",
            createdAt: "2026-04-01T10:00:00Z",
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: articleTitle,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: hasPendingMessage,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil,
            councilMode: true,
            activeChildSessionId: activeChildSessionId
        )
    }
}

@MainActor
private extension ChatDependencies {
    static func test(
        transcriptionService: any SpeechTranscribing,
        chatService: any ChatSessionServicing = MockChatSessionService()
    ) -> ChatDependencies {
        ChatDependencies(
            chatService: chatService,
            transcriptionService: transcriptionService,
            activeSessionManager: .shared
        )
    }
}

private final class MockChatSessionService: ChatSessionServicing {
    private let sendMessageHandler: ((Int, String) async throws -> SendChatMessageResponse)?
    private let selectCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)?
    private let retryCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)?

    init(
        sendMessageHandler: ((Int, String) async throws -> SendChatMessageResponse)? = nil,
        selectCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)? = nil,
        retryCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)? = nil
    ) {
        self.sendMessageHandler = sendMessageHandler
        self.selectCouncilBranchHandler = selectCouncilBranchHandler
        self.retryCouncilBranchHandler = retryCouncilBranchHandler
    }

    func getSession(id: Int) async throws -> ChatSessionDetail {
        throw ChatServiceError.timeout
    }

    func sendMessageAsync(sessionId: Int, message: String) async throws -> SendChatMessageResponse {
        if let sendMessageHandler {
            return try await sendMessageHandler(sessionId, message)
        }
        throw ChatServiceError.timeout
    }

    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        throw ChatServiceError.timeout
    }

    func getInitialSuggestions(sessionId: Int) async throws -> ChatMessage {
        throw ChatServiceError.timeout
    }

    func startCouncil(sessionId: Int, message: String) async throws -> ChatSessionDetail {
        throw ChatServiceError.timeout
    }

    func selectCouncilBranch(sessionId: Int, childSessionId: Int) async throws -> ChatSessionDetail {
        if let selectCouncilBranchHandler {
            return try await selectCouncilBranchHandler(sessionId, childSessionId)
        }
        throw ChatServiceError.timeout
    }

    func retryCouncilBranch(sessionId: Int, childSessionId: Int) async throws -> ChatSessionDetail {
        if let retryCouncilBranchHandler {
            return try await retryCouncilBranchHandler(sessionId, childSessionId)
        }
        throw ChatServiceError.timeout
    }

    func updateSessionProvider(sessionId: Int, provider: ChatModelProvider) async throws -> ChatSessionSummary {
        throw ChatServiceError.timeout
    }
}

@MainActor
private final class MockChatSpeechTranscriber: SpeechTranscribing {
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

    private let transcript: String

    init(transcript: String) {
        self.transcript = transcript
    }

    func start() async throws {
        startCallCount += 1
        isRecording = true
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
        isRecording = false
        isTranscribing = false
        onStateChange?(.idle)
    }
}
