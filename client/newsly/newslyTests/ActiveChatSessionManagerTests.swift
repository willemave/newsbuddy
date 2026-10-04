import XCTest
@testable import newsly

@MainActor
final class ActiveChatSessionManagerTests: XCTestCase {
    func testStartTrackingUpdatesProcessingStateAndContentLookup() {
        let manager = makeTestActiveChatSessionManager()

        manager.startTracking(
            session: Self.session(id: 42),
            contentId: 7,
            contentTitle: "Tracked Article",
            messageId: 501
        )

        XCTAssertTrue(!manager.activeSessions.isEmpty)
        XCTAssertEqual(manager.activeSessions.count, 1)
        XCTAssertEqual(manager.getSession(forContentId: 7)?.id, 42)
        XCTAssertEqual(manager.getSession(forContentId: 7)?.messageId, 501)
    }

    func testNewestSessionForContentWinsUntilStopped() {
        let manager = makeTestActiveChatSessionManager()

        manager.startTracking(
            session: Self.session(id: 42),
            contentId: 7,
            contentTitle: "First Article",
            messageId: 501
        )
        manager.startTracking(
            session: Self.session(id: 43),
            contentId: 7,
            contentTitle: "Second Article",
            messageId: 502
        )

        XCTAssertEqual(manager.getSession(forContentId: 7)?.id, 43)

        manager.stopTracking(sessionId: 43)

        XCTAssertEqual(manager.getSession(forContentId: 7)?.id, 42)
        XCTAssertEqual(manager.activeSessions.count, 1)
    }

    func testResetClearsTrackedSessions() {
        let manager = makeTestActiveChatSessionManager()

        manager.startTracking(
            session: Self.session(id: 42),
            contentId: 7,
            contentTitle: "Tracked Article",
            messageId: 501
        )

        manager.reset()

        XCTAssertFalse(!manager.activeSessions.isEmpty)
        XCTAssertEqual(manager.activeSessions.count, 0)
        XCTAssertNil(manager.getSession(forContentId: 7))
    }

    func testSupersededMessageCannotCompleteOrFailReplacementSession() {
        let manager = makeTestActiveChatSessionManager()
        manager.startTracking(
            session: Self.session(id: 42),
            contentId: 7,
            contentTitle: "Tracked Article",
            messageId: 501
        )
        manager.startTracking(
            session: Self.session(id: 42),
            contentId: 7,
            contentTitle: "Tracked Article",
            messageId: 502
        )

        manager.handleCompletion(sessionId: 42, messageId: 501)
        manager.handleFailure(sessionId: 42, messageId: 501, error: "Old failure")

        XCTAssertEqual(manager.activeSessions[42]?.messageId, 502)
        XCTAssertEqual(manager.activeSessions[42]?.status, .processing)
        XCTAssertNil(manager.completedSessions[42])
    }

    func testLifecycleSuspensionPausesAndResumesPollingWithoutDroppingTrackedSession() async {
        let service = PollingChatSessionService()
        let registry = ChatMessageCompletionRegistry(
            statusService: service,
            policy: ChatMessageCompletionPollingPolicy(
                delaysNanoseconds: Array(repeating: 20_000_000, count: 99)
            ),
            orphanGraceNanoseconds: 0
        )
        let manager = makeTestActiveChatSessionManager(
            messageCompletionRegistry: registry,
            startsPolling: true
        )

        manager.startTracking(
            session: Self.session(id: 42),
            contentId: 7,
            contentTitle: "Tracked Article",
            messageId: 501
        )

        let didStartPolling = await waitUntil { await service.messageStatusCallCount() > 0 }
        XCTAssertTrue(didStartPolling)

        manager.setPollingSuspended(true)
        let pausedCallCount = await service.messageStatusCallCount()
        try? await Task.sleep(nanoseconds: 80_000_000)
        let callCountAfterPause = await service.messageStatusCallCount()

        XCTAssertEqual(callCountAfterPause, pausedCallCount)
        XCTAssertEqual(manager.getSession(forContentId: 7)?.id, 42)

        manager.setPollingSuspended(false)

        let didResumePolling = await waitUntil { await service.messageStatusCallCount() > pausedCallCount }
        XCTAssertTrue(didResumePolling)

        manager.reset()
    }

    private static func session(id: Int) -> ChatSessionSummary {
        .fixture(
            id: id,
            createdAt: ServerDate.parse("2026-04-01T10:00:00Z")!
        )
    }

    private func waitUntil(
        _ condition: @escaping () async -> Bool,
        timeout: TimeInterval = 1.0
    ) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await condition() {
                return true
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        return await condition()
    }
}

private final class PollingChatSessionService: ChatSessionServicing {
    private let callCounter = PollingCallCounter()

    func messageStatusCallCount() async -> Int {
        await callCounter.value
    }

    func getSession(id: Int) async throws -> ChatSessionDetail {
        throw ChatServiceError.timeout
    }

    func sendMessageAsync(sessionId: Int, message: String) async throws -> SendChatMessageResponse {
        throw ChatServiceError.timeout
    }

    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse {
        await callCounter.increment()
        return MessageStatusResponse(messageId: messageId, status: .processing)
    }

    func getInitialSuggestions(sessionId: Int) async throws -> ChatMessage {
        throw ChatServiceError.timeout
    }

    func updateSessionProvider(sessionId: Int, provider: ChatModelProvider) async throws -> ChatSessionSummary {
        throw ChatServiceError.timeout
    }
}

private actor PollingCallCounter {
    private var count = 0

    var value: Int {
        count
    }

    func increment() {
        count += 1
    }
}
