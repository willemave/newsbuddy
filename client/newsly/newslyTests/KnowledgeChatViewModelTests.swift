import Foundation
import XCTest
@testable import newsly

@MainActor
final class KnowledgeChatViewModelTests: XCTestCase {
    func testVoiceMicStopsRecordingAndStartsAssistantTurnWithTranscript() async {
        let transcriptionService = MockKnowledgeSpeechTranscriber(transcript: "What should I read next?")
        let chatService = MockKnowledgeChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 91))]
        )
        let viewModel = KnowledgeChatViewModel(
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
        XCTAssertEqual(chatService.receivedScreenTypes, ["knowledge_hub"])
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 1)
        XCTAssertFalse(viewModel.isVoiceRecording)
        XCTAssertFalse(viewModel.isVoiceTranscribing)

        viewModel.cancelVoiceRecording()
        XCTAssertEqual(transcriptionService.resetCallCount, 0)
    }

    func testVoiceSilenceAutoStopPublishesCompletedRoute() async {
        let transcriptionService = MockKnowledgeSpeechTranscriber(transcript: "Summarize my unread stories")
        let chatService = MockKnowledgeChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 92))]
        )
        let viewModel = KnowledgeChatViewModel(
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

    func testVoiceMaximumDurationPublishesCompletedRoute() async {
        let transcriptionService = MockKnowledgeSpeechTranscriber(transcript: "Summarize the day")
        let chatService = MockKnowledgeChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 93))]
        )
        let viewModel = KnowledgeChatViewModel(
            chatService: chatService,
            transcriptionService: transcriptionService,
            initialVoiceDictationAvailable: true
        )

        _ = await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateAutomaticStop(reason: .maximumDuration)
        for _ in 0..<100 where viewModel.completedVoiceRoute == nil {
            await Task.yield()
        }

        XCTAssertEqual(viewModel.completedVoiceRoute?.sessionId, 93)
        XCTAssertEqual(chatService.receivedMessages, ["Summarize the day"])
    }

    func testVoiceNoSpeechFailureCanRestartRecording() async {
        let transcriptionService = MockKnowledgeSpeechTranscriber(transcript: "Try again")
        let viewModel = KnowledgeChatViewModel(
            chatService: MockKnowledgeChatService(turnResponses: []),
            transcriptionService: transcriptionService,
            initialVoiceDictationAvailable: true
        )

        _ = await viewModel.toggleVoiceRecording()
        await transcriptionService.simulateNoSpeechTimeout()
        for _ in 0..<100 where viewModel.errorMessage == nil {
            await Task.yield()
        }
        for _ in 0..<100 where transcriptionService.hasActiveSession {
            await Task.yield()
        }

        XCTAssertEqual(viewModel.errorMessage, "No speech detected. Try again.")
        _ = await viewModel.toggleVoiceRecording()
        XCTAssertTrue(viewModel.isVoiceRecording)
        XCTAssertNil(viewModel.errorMessage)
        viewModel.cancelVoiceRecording()
    }

    func testEmptyVoiceTranscriptShowsRetryableErrorWithoutCreatingChat() async {
        let transcriptionService = MockKnowledgeSpeechTranscriber(transcript: "  ")
        let chatService = MockKnowledgeChatService(turnResponses: [])
        let viewModel = KnowledgeChatViewModel(
            chatService: chatService,
            transcriptionService: transcriptionService,
            initialVoiceDictationAvailable: true
        )

        _ = await viewModel.toggleVoiceRecording()
        let route = await viewModel.toggleVoiceRecording()

        XCTAssertNil(route)
        XCTAssertEqual(viewModel.errorMessage, "I didn't catch that. Try again.")
        XCTAssertTrue(chatService.receivedMessages.isEmpty)
    }

    func testVoiceStartAndTranscriptionFailuresReleaseForRetry() async {
        let startFailure = MockKnowledgeSpeechTranscriber(
            transcript: "unused",
            startError: VoiceDictationError.recordingFailed
        )
        let startViewModel = KnowledgeChatViewModel(
            chatService: MockKnowledgeChatService(turnResponses: []),
            transcriptionService: startFailure,
            initialVoiceDictationAvailable: true
        )

        _ = await startViewModel.toggleVoiceRecording()
        XCTAssertEqual(startViewModel.errorMessage, "Failed to record audio.")
        XCTAssertFalse(startViewModel.isVoiceRecording)

        let transcriptionFailure = MockKnowledgeSpeechTranscriber(
            transcript: "unused",
            stopError: VoiceDictationError.transcriptionFailed("scripted failure")
        )
        let stopViewModel = KnowledgeChatViewModel(
            chatService: MockKnowledgeChatService(turnResponses: []),
            transcriptionService: transcriptionFailure,
            initialVoiceDictationAvailable: true
        )

        _ = await stopViewModel.toggleVoiceRecording()
        _ = await stopViewModel.toggleVoiceRecording()
        XCTAssertEqual(
            stopViewModel.errorMessage,
            "Transcription failed: scripted failure"
        )
        XCTAssertFalse(stopViewModel.isVoiceTranscribing)
    }

    func testCancelVoiceRecordingResetsTranscriberAndState() async {
        let transcriptionService = MockKnowledgeSpeechTranscriber(transcript: "Ignore this")
        let viewModel = KnowledgeChatViewModel(
            chatService: MockKnowledgeChatService(turnResponses: []),
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

    func testStartChatCreatesAssistantTurnWithKnowledgeContext() async {
        let chatService = MockKnowledgeChatService(
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 91))]
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)

        let route = await viewModel.startChat(message: "What changed this week?")

        XCTAssertEqual(route?.sessionId, 91)
        XCTAssertEqual(route?.initialUserMessageText, "Prompt")
        XCTAssertEqual(route?.initialUserMessageTimestamp, ServerDate.parse("2026-03-21T18:00:00Z"))
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

    func testLoadChatsStoresFirstHistoryPageAndPagination() async {
        let sessions = [
            makeSession(id: 1),
            makeSession(id: 2),
            makeSession(id: 3),
        ]
        let chatService = MockKnowledgeChatService(
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
        let viewModel = KnowledgeChatViewModel(chatService: chatService)

        await viewModel.loadChats()

        XCTAssertEqual(chatService.requestedPageLimits, [20])
        XCTAssertEqual(chatService.requestedPageCursors, [nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2, 3])
        XCTAssertTrue(viewModel.hasMoreSessions)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testActiveChatWorkIncludesShareChatWaitingForContent() async {
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1, isWaitingForContent: true)],
                        nextCursor: nil,
                        hasMore: false
                    )
                )
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)

        await viewModel.loadChats()

        XCTAssertTrue(viewModel.hasActiveChatWork)
        XCTAssertTrue(viewModel.sessions[0].isPreparingChat)
    }

    func testActiveChatPollingStopsWhenSessionFinishesPreparing() async {
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1, isWaitingForContent: true)],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1)],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeChatViewModel(
            chatService: chatService,
            activeChatPollIntervalNanoseconds: 1_000_000
        )

        await viewModel.loadChats()
        await viewModel.pollActiveChatWork()

        XCTAssertFalse(viewModel.hasActiveChatWork)
        XCTAssertEqual(chatService.requestedPageLimits.count, 2)
    }

    func testLoadChatsIgnoresCancelledRefreshAndKeepsCurrentSessions() async {
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1), makeSession(id: 2)],
                        nextCursor: "next-page",
                        hasMore: true
                    )
                ),
                .failure(ClientFailure.cancelled),
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)

        await viewModel.loadChats()
        await viewModel.loadChats()

        XCTAssertEqual(chatService.requestedPageCursors, [nil, nil])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2])
        XCTAssertTrue(viewModel.hasMoreSessions)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testRefreshKeepsCurrentSessionsVisibleWhileLoading() async {
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1)],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 2)],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)
        await viewModel.loadChats()

        chatService.pauseNextPageResponse()
        let refreshTask = Task { await viewModel.loadChats() }
        defer {
            refreshTask.cancel()
            chatService.resumePageResponse()
        }
        await chatService.waitForPageResponsePause()

        XCTAssertTrue(viewModel.isLoading)
        XCTAssertEqual(viewModel.sessions.map(\.id), [1])

        chatService.resumePageResponse()
        await refreshTask.value

        XCTAssertFalse(viewModel.isLoading)
        XCTAssertEqual(viewModel.sessions.map(\.id), [2])
    }

    func testLoadMoreAppendsUniqueSessions() async {
        let chatService = MockKnowledgeChatService(
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
        let viewModel = KnowledgeChatViewModel(chatService: chatService)

        await viewModel.loadChats()
        await viewModel.loadMoreSessions()

        XCTAssertEqual(chatService.requestedPageCursors, [nil, "next-page"])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2, 3])
        XCTAssertFalse(viewModel.hasMoreSessions)
        XCTAssertFalse(viewModel.hasLoadMoreError)
    }

    func testNestedCancellationDuringLoadMoreKeepsCursorForRetry() async {
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1)],
                        nextCursor: "next-page",
                        hasMore: true
                    )
                ),
                .failure(
                    AuthError.networkError(URLError(.cancelled))
                ),
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 2)],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)

        await viewModel.loadChats()
        await viewModel.loadMoreSessions()

        XCTAssertEqual(viewModel.sessions.map(\.id), [1])
        XCTAssertTrue(viewModel.hasMoreSessions)
        XCTAssertFalse(viewModel.hasLoadMoreError)

        await viewModel.loadMoreSessions()

        XCTAssertEqual(chatService.requestedPageCursors, [nil, "next-page", "next-page"])
        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2])
        XCTAssertFalse(viewModel.hasMoreSessions)
        XCTAssertFalse(viewModel.hasLoadMoreError)
    }

    func testStartChatStoresErrorWhenAssistantTurnFails() async {
        let chatService = MockKnowledgeChatService(
            turnResponses: [.failure(MockKnowledgeChatService.MockError.boom)]
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)

        let route = await viewModel.startChat(message: "Find me something new")

        XCTAssertNil(route)
        XCTAssertEqual(viewModel.errorMessage, "Boom")
    }

    func testDeleteSessionRemovesItFromKnowledgeHistory() async {
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1), makeSession(id: 2)],
                        nextCursor: nil,
                        hasMore: false
                    )
                )
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)
        await viewModel.loadChats()

        await viewModel.deleteSession(viewModel.sessions[0])

        XCTAssertEqual(chatService.deletedSessionIDs, [1])
        XCTAssertEqual(viewModel.sessions.map(\.id), [2])
        XCTAssertNil(viewModel.errorMessage)
    }

    func testFailedDeleteKeepsSessionInKnowledgeHistory() async {
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [makeSession(id: 1), makeSession(id: 2)],
                        nextCursor: nil,
                        hasMore: false
                    )
                )
            ],
            turnResponses: [],
            deleteError: MockKnowledgeChatService.MockError.boom
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)
        await viewModel.loadChats()

        await viewModel.deleteSession(viewModel.sessions[0])

        XCTAssertEqual(viewModel.sessions.map(\.id), [1, 2])
        XCTAssertEqual(viewModel.errorMessage, "Boom")
    }

    func testCompletedDeleteIsNotResurrectedByStaleRefresh() async {
        let staleSessions = [makeSession(id: 1), makeSession(id: 2)]
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: staleSessions,
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
                .success(
                    makeSessionListResponse(
                        sessions: staleSessions,
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
            ],
            turnResponses: []
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)
        await viewModel.loadChats()

        chatService.pauseNextPageResponse()
        let refreshTask = Task { await viewModel.loadChats() }
        defer {
            refreshTask.cancel()
            chatService.resumePageResponse()
        }
        await chatService.waitForPageResponsePause()

        await viewModel.deleteSession(viewModel.sessions[0])
        chatService.resumePageResponse()
        await refreshTask.value

        XCTAssertEqual(viewModel.sessions.map(\.id), [2])
        XCTAssertEqual(chatService.deletedSessionIDs, [1])
    }

    func testCreatedChatIsNotDroppedByRefreshThatStartedBeforeCreation() async {
        let existingSession = makeSession(id: 1)
        let chatService = MockKnowledgeChatService(
            pageResponses: [
                .success(
                    makeSessionListResponse(
                        sessions: [existingSession],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
                .success(
                    makeSessionListResponse(
                        sessions: [existingSession],
                        nextCursor: nil,
                        hasMore: false
                    )
                ),
            ],
            turnResponses: [.success(makeAssistantTurnResponse(sessionId: 91))]
        )
        let viewModel = KnowledgeChatViewModel(chatService: chatService)
        await viewModel.loadChats()

        chatService.pauseNextPageResponse()
        let refreshTask = Task { await viewModel.loadChats() }
        defer {
            refreshTask.cancel()
            chatService.resumePageResponse()
        }
        await chatService.waitForPageResponsePause()

        _ = await viewModel.startChat(message: "What changed?")
        chatService.resumePageResponse()
        await refreshTask.value

        XCTAssertEqual(viewModel.sessions.map(\.id), [91, 1])
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

    private func makeSession(
        id: Int,
        sessionType: String = "knowledge_chat",
        isWaitingForContent: Bool = false
    ) -> ChatSessionSummary {
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
            isWaitingForContent: isWaitingForContent,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }
}

@MainActor
private final class MockKnowledgeSpeechTranscriber: SpeechTranscribing {
    var isAvailable = true
    private(set) var startCallCount = 0
    private(set) var stopCallCount = 0
    private(set) var resetCallCount = 0

    private let transcript: String
    private let startError: Error?
    private let stopError: Error?
    private var activeSessionID: UUID?
    private var continuation: AsyncStream<SpeechTranscriptionEvent>.Continuation?

    var hasActiveSession: Bool { activeSessionID != nil }

    init(
        transcript: String,
        startError: Error? = nil,
        stopError: Error? = nil
    ) {
        self.transcript = transcript
        self.startError = startError
        self.stopError = stopError
    }

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        _ = deadlines
        guard activeSessionID == nil else { throw VoiceDictationError.sessionBusy }
        let sessionID = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSessionID = sessionID
        continuation = pair.continuation
        return SpeechTranscriptionSession(
            id: sessionID,
            events: pair.stream,
            start: { [weak self] id in
                guard let self else { throw VoiceDictationError.noActiveSession }
                try self.start(sessionID: id)
            },
            stop: { [weak self] id in
                guard let self else { throw VoiceDictationError.noActiveSession }
                return try self.stop(sessionID: id)
            },
            cancel: { [weak self] id in self?.cancel(sessionID: id) }
        )
    }

    func simulateSilenceAutoStop() async {
        await simulateAutomaticStop(reason: .silenceAutoStop)
    }

    func simulateAutomaticStop(reason: SpeechStopReason) async {
        emit(.stateChange(.transcribing))
        emit(.transcriptFinal(transcript))
        emit(.stateChange(.idle))
        emit(.stopReason(reason))
        releaseSession()
        await Task.yield()
    }

    func simulateNoSpeechTimeout() async {
        let message = "No speech detected. Try again."
        emit(.stateChange(.failed(message)))
        emit(.error(message))
        emit(.stopReason(.noSpeechTimeout))
        releaseSession()
        await Task.yield()
    }

    private func start(sessionID: UUID) throws {
        guard activeSessionID == sessionID else { throw VoiceDictationError.noActiveSession }
        startCallCount += 1
        if let startError {
            releaseSession()
            throw startError
        }
        emit(.stateChange(.recording))
    }

    private func stop(sessionID: UUID) throws -> String {
        guard activeSessionID == sessionID else { throw VoiceDictationError.noActiveSession }
        stopCallCount += 1
        if let stopError {
            releaseSession()
            throw stopError
        }
        releaseSession()
        return transcript
    }

    private func cancel(sessionID: UUID) {
        guard activeSessionID == sessionID else { return }
        resetCallCount += 1
        emit(.stateChange(.idle))
        emit(.stopReason(.cancel))
        releaseSession()
    }

    private func emit(_ event: SpeechTranscriptionEvent) {
        continuation?.yield(event)
    }

    private func releaseSession() {
        activeSessionID = nil
        continuation?.finish()
        continuation = nil
    }
}

@MainActor
private final class MockKnowledgeChatService: KnowledgeChatServicing {
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
    var deletedSessionIDs: [Int] = []

    private var pageResponses: [Result<ChatSessionListResponse, Error>]
    private var turnResponses: [Result<AssistantTurnResponse, Error>]
    private let deleteError: Error?
    private var shouldPauseNextPageResponse = false
    private var pageResponseContinuation: CheckedContinuation<Void, Never>?
    private var pageResponsePausedContinuation: CheckedContinuation<Void, Never>?

    init(
        pageResponses: [Result<ChatSessionListResponse, Error>] = [],
        turnResponses: [Result<AssistantTurnResponse, Error>],
        deleteError: Error? = nil
    ) {
        self.pageResponses = pageResponses
        self.turnResponses = turnResponses
        self.deleteError = deleteError
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

        let response = try pageResponses.removeFirst().get()
        if shouldPauseNextPageResponse {
            shouldPauseNextPageResponse = false
            await withCheckedContinuation { continuation in
                pageResponseContinuation = continuation
                pageResponsePausedContinuation?.resume()
                pageResponsePausedContinuation = nil
            }
        }
        return response
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

    func deleteSession(sessionId: Int) async throws {
        deletedSessionIDs.append(sessionId)
        if let deleteError {
            throw deleteError
        }
    }

    func pauseNextPageResponse() {
        shouldPauseNextPageResponse = true
    }

    func waitForPageResponsePause() async {
        guard pageResponseContinuation == nil else { return }
        await withCheckedContinuation { continuation in
            pageResponsePausedContinuation = continuation
        }
    }

    func resumePageResponse() {
        pageResponseContinuation?.resume()
        pageResponseContinuation = nil
    }

}
