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

    func testToggleVoiceRecordingStopsRecordingOnSecondTapAndSendsTranscript() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.toggleVoiceRecording()
        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["Final transcript"])
        XCTAssertEqual(viewModel.inputText, "")
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

    func testStopVoiceRecordingSendsTranscriptWithoutDraftPreview() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        viewModel.isRecording = true
        XCTAssertEqual(viewModel.inputText, "")

        await viewModel.stopVoiceRecording()

        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["Final transcript"])
        XCTAssertEqual(viewModel.inputText, "")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(viewModel.timeline.map(\.message.content), ["Final transcript", "Assistant reply"])
    }

    func testStopVoiceRecordingSendsExistingDraftAndTranscript() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "second thought")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        viewModel.inputText = "First draft"
        viewModel.isRecording = true

        await viewModel.stopVoiceRecording()

        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["First draft second thought"])
        XCTAssertEqual(viewModel.inputText, "")
    }

    func testSilenceAutoStopSendsTranscriptWithoutManualStop() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Auto transcript")
        let chatService = makeSuccessfulVoiceSendService()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: transcriptionService,
                chatService: chatService
            ),
            initialVoiceDictationAvailable: true
        )

        await viewModel.startVoiceRecording()
        await transcriptionService.simulateSilenceAutoStop()
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(chatService.sentMessages.map { $0.message }, ["Auto transcript"])
        XCTAssertEqual(viewModel.inputText, "")
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

        let didStartSelection = await waitUntil { viewModel.selectingCouncilChildSessionId == 201 }
        XCTAssertTrue(didStartSelection)

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
            timestamp: ServerDate.parse("2026-04-01T10:00:00Z")!,
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

    func testHandleDisappearKeepsPreAckSendAliveAndHandsOffAfterServerAck() async {
        let ackGate = AsyncGate()
        let chatService = MockChatSessionService(
            sendMessageHandler: { sessionId, message in
                await ackGate.wait()
                return SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(id: 101, role: .user, content: message, status: .processing),
                    messageId: 501,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                MessageStatusResponse(
                    messageId: messageId,
                    status: .completed,
                    assistantMessage: Self.message(
                        id: 201,
                        role: .assistant,
                        content: "Should not poll while inactive",
                        status: .completed
                    ),
                    error: nil
                )
            }
        )
        ActiveChatSessionManager.shared.reset()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.inputText = "Hello"
        viewModel.performSendMessage()
        let didStartSend = await waitUntil {
            chatService.sentMessages.count == 1 && viewModel.isSending
        }

        XCTAssertTrue(didStartSend)
        XCTAssertTrue(viewModel.isSending)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")

        viewModel.handleDisappear()

        XCTAssertTrue(viewModel.isSending)
        XCTAssertNil(ActiveChatSessionManager.shared.getSession(forContentId: 7))

        await ackGate.open()
        let didHandOff = await waitUntil {
            ActiveChatSessionManager.shared.getSession(forContentId: 7)?.messageId == 501
        }

        XCTAssertTrue(didHandOff)
        XCTAssertFalse(viewModel.isSending)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")
        XCTAssertEqual(chatService.messageStatusCallCount, 0)
        ActiveChatSessionManager.shared.reset()
    }

    func testHandleDisappearCancelsAcceptedPollingWithoutFailingMessage() async {
        let chatService = MockChatSessionService(
            sendMessageHandler: { sessionId, message in
                SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(id: 101, role: .user, content: message, status: .processing),
                    messageId: 501,
                    status: .processing
                )
            },
            messageStatusHandler: { messageId in
                while true {
                    try Task.checkCancellation()
                    try await Task.sleep(nanoseconds: 10_000_000)
                }
            }
        )
        ActiveChatSessionManager.shared.reset()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.inputText = "Hello"
        viewModel.performSendMessage()
        let didStartPolling = await waitUntil {
            chatService.messageStatusCallCount > 0 && viewModel.isSending
        }

        XCTAssertTrue(didStartPolling)
        XCTAssertTrue(viewModel.isSending)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")

        viewModel.handleDisappear()
        let didCancelPolling = await waitUntil { !viewModel.isSending }

        XCTAssertTrue(didCancelPolling)
        XCTAssertFalse(viewModel.isSending)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Hello")
        XCTAssertFalse(viewModel.timeline.last?.message.hasFailed ?? true)
        XCTAssertEqual(ActiveChatSessionManager.shared.getSession(forContentId: 7)?.messageId, 501)
        ActiveChatSessionManager.shared.reset()
    }

    func testSendMessageSurfacesTransportErrorWhenNotCancelled() async {
        let chatService = MockChatSessionService(sendMessageHandler: { _, _ in
            throw APIError.networkError(URLError(.networkConnectionLost))
        })
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.inputText = "Hello"

        await viewModel.sendMessage()

        XCTAssertNotNil(viewModel.errorMessage)
        XCTAssertTrue(viewModel.timeline.last?.message.hasFailed ?? false)
        XCTAssertEqual(viewModel.timeline.last?.retryText, "Hello")
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
                initialUserMessageTimestamp: ServerDate.parse("2026-04-01T10:00:00Z")!,
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

    func testHandleDisappearBeforeServerAckDoesNotTrackLocalPlaceholder() async {
        let ackGate = AsyncGate()
        let chatService = MockChatSessionService(sendMessageHandler: { _, _ in
            await ackGate.wait()
            throw CancellationError()
        })
        ActiveChatSessionManager.shared.reset()
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(session: Self.session(
                contentId: 7,
                articleTitle: "Tracked Article"
            )),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        viewModel.inputText = "Track only after backend ack"
        viewModel.performSendMessage()
        let didStartSend = await waitUntil {
            chatService.sentMessages.count == 1 && viewModel.isSending
        }

        XCTAssertTrue(didStartSend)
        XCTAssertTrue(viewModel.isSending)
        XCTAssertEqual(viewModel.timeline.last?.message.content, "Track only after backend ack")

        viewModel.handleDisappear()

        XCTAssertNil(ActiveChatSessionManager.shared.getSession(forContentId: 7))
        await ackGate.open()
        _ = await waitUntil { !viewModel.isSending }
        ActiveChatSessionManager.shared.reset()
    }

    func testEmptyContextualSessionDoesNotAutoGenerateInitialSuggestions() async {
        let chatService = MockChatSessionService(getSessionHandler: { _ in
            ChatSessionDetail(
                session: Self.session(
                    contentId: 7,
                    articleTitle: "Tracked Article",
                    hasMessages: false,
                    councilMode: false
                ),
                messages: []
            )
        })
        let viewModel = ChatSessionViewModel(
            route: ChatSessionRoute(sessionId: 42),
            dependencies: .test(
                transcriptionService: MockChatSpeechTranscriber(transcript: "Ignored"),
                chatService: chatService
            )
        )

        await viewModel.loadSession()

        XCTAssertEqual(chatService.initialSuggestionsCallCount, 0)
        XCTAssertFalse(viewModel.isSending)
        XCTAssertTrue(viewModel.timeline.isEmpty)
        XCTAssertEqual(viewModel.session?.contentId, 7)
    }

    private func makeSuccessfulVoiceSendService() -> MockChatSessionService {
        var latestMessage = ""
        return MockChatSessionService(
            getSessionHandler: { _ in
                ChatSessionDetail(
                    session: Self.session(),
                    messages: [
                        Self.message(id: 101, role: .user, content: latestMessage, status: .completed),
                        Self.message(id: 201, role: .assistant, content: "Assistant reply", status: .completed),
                    ]
                )
            },
            sendMessageHandler: { sessionId, message in
                latestMessage = message
                return SendChatMessageResponse(
                    sessionId: sessionId,
                    userMessage: Self.message(id: 101, role: .user, content: message, status: .processing),
                    messageId: 501,
                    status: .processing
                )
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

    private static func session(
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        articleTitle: String? = nil,
        hasPendingMessage: Bool = false,
        activeChildSessionId: Int? = nil,
        hasMessages: Bool = true,
        councilMode: Bool? = true
    ) -> ChatSessionSummary {
        ChatSessionSummary(
            id: 42,
            contentId: contentId,
            newsItemId: newsItemId,
            title: "Chat",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.5",
            createdAt: ServerDate.parse("2026-04-01T10:00:00Z")!,
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: articleTitle,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: hasPendingMessage,
            isSavedToKnowledge: false,
            hasMessages: hasMessages,
            lastMessagePreview: nil,
            lastMessageRole: nil,
            councilMode: councilMode,
            activeChildSessionId: activeChildSessionId
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
    private let getSessionHandler: ((Int) async throws -> ChatSessionDetail)?
    private let sendMessageHandler: ((Int, String) async throws -> SendChatMessageResponse)?
    private let messageStatusHandler: ((Int) async throws -> MessageStatusResponse)?
    private let selectCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)?
    private let retryCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)?
    private(set) var initialSuggestionsCallCount = 0
    private(set) var messageStatusCallCount = 0
    private(set) var sentMessages: [(sessionId: Int, message: String)] = []

    init(
        getSessionHandler: ((Int) async throws -> ChatSessionDetail)? = nil,
        sendMessageHandler: ((Int, String) async throws -> SendChatMessageResponse)? = nil,
        messageStatusHandler: ((Int) async throws -> MessageStatusResponse)? = nil,
        selectCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)? = nil,
        retryCouncilBranchHandler: ((Int, Int) async throws -> ChatSessionDetail)? = nil
    ) {
        self.getSessionHandler = getSessionHandler
        self.sendMessageHandler = sendMessageHandler
        self.messageStatusHandler = messageStatusHandler
        self.selectCouncilBranchHandler = selectCouncilBranchHandler
        self.retryCouncilBranchHandler = retryCouncilBranchHandler
    }

    func getSession(id: Int) async throws -> ChatSessionDetail {
        if let getSessionHandler {
            return try await getSessionHandler(id)
        }
        throw ChatServiceError.timeout
    }

    func sendMessageAsync(sessionId: Int, message: String) async throws -> SendChatMessageResponse {
        sentMessages.append((sessionId: sessionId, message: message))
        if let sendMessageHandler {
            return try await sendMessageHandler(sessionId, message)
        }
        throw ChatServiceError.timeout
    }

    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        messageStatusCallCount += 1
        if let messageStatusHandler {
            return try await messageStatusHandler(messageId)
        }
        throw ChatServiceError.timeout
    }

    func getInitialSuggestions(sessionId: Int) async throws -> ChatMessage {
        initialSuggestionsCallCount += 1
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
