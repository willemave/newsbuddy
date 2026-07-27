import XCTest
@testable import newsly

final class ChatMessageCompletionRegistryTests: XCTestCase {
    func testSimultaneousObserversShareOneFetchSequence() async throws {
        let firstFetchGate = CompletionRegistryGate()
        let source = ScriptedMessageStatusSource(
            responses: [
                .processing,
                .processing,
                .completed(Self.assistantMessage()),
            ],
            firstFetchGate: firstFetchGate
        )
        let sleeper = ControlledCompletionSleeper()
        let progress = CompletionProgressRecorder()
        let registry = ChatMessageCompletionRegistry(
            fetchStatus: { try await source.fetch(messageId: $0) },
            policy: ChatMessageCompletionPollingPolicy(delaysNanoseconds: [1, 2]),
            sleep: { try await sleeper.sleep(nanoseconds: $0) }
        )

        let firstObserver = Task {
            try await registry.waitForCompletion(messageId: 501) { attempt in
                await progress.record(attempt)
            }
        }
        let didStartFirstFetch = await waitUntil { await source.callCount == 1 }
        XCTAssertTrue(didStartFirstFetch)

        let secondObserver = Task {
            try await registry.waitForCompletion(messageId: 501)
        }
        let didAttachBothObservers = await waitUntil {
            await registry.activeObserverCount(messageId: 501) == 2
        }
        XCTAssertTrue(didAttachBothObservers)

        await firstFetchGate.open()
        let didReachFirstDelay = await waitUntil { await sleeper.recordedDelays.count == 1 }
        XCTAssertTrue(didReachFirstDelay)
        await sleeper.resumeNext()
        let didReachSecondDelay = await waitUntil { await sleeper.recordedDelays.count == 2 }
        XCTAssertTrue(didReachSecondDelay)
        await sleeper.resumeNext()

        let firstMessage = try await firstObserver.value
        let secondMessage = try await secondObserver.value

        XCTAssertEqual(firstMessage.id, 901)
        XCTAssertEqual(secondMessage.id, 901)
        let fetchCount = await source.callCount
        let recordedDelays = await sleeper.recordedDelays
        let progressAttempts = await progress.attempts
        XCTAssertEqual(fetchCount, 3)
        XCTAssertEqual(recordedDelays, [1, 2])
        XCTAssertEqual(progressAttempts, [1, 2])
    }

    func testObserverCancellationHandsOffWithoutRestartingPolling() async throws {
        let source = ScriptedMessageStatusSource(
            responses: [
                .processing,
                .completed(Self.assistantMessage()),
            ]
        )
        let sleeper = ControlledCompletionSleeper()
        let registry = ChatMessageCompletionRegistry(
            fetchStatus: { try await source.fetch(messageId: $0) },
            policy: ChatMessageCompletionPollingPolicy(delaysNanoseconds: [1]),
            sleep: { try await sleeper.sleep(nanoseconds: $0) },
            orphanGraceNanoseconds: 1_000_000_000
        )

        let foregroundObserver = Task {
            try await registry.waitForCompletion(messageId: 501)
        }
        let didReachDelay = await waitUntil { await sleeper.recordedDelays.count == 1 }
        XCTAssertTrue(didReachDelay)

        foregroundObserver.cancel()
        do {
            _ = try await foregroundObserver.value
            XCTFail("Expected the cancelled observer to stop waiting")
        } catch is CancellationError {
            // Expected. The registry retains the poll briefly for ownership handoff.
        }

        let didDetachForegroundObserver = await waitUntil {
            await registry.activeObserverCount(messageId: 501) == 0
        }
        XCTAssertTrue(didDetachForegroundObserver)

        let backgroundObserver = Task {
            try await registry.waitForCompletion(messageId: 501)
        }
        let didAttachBackgroundObserver = await waitUntil {
            await registry.activeObserverCount(messageId: 501) == 1
        }
        XCTAssertTrue(didAttachBackgroundObserver)

        await sleeper.resumeNext()
        let message = try await backgroundObserver.value
        XCTAssertEqual(message.id, 901)
        let fetchCountAfterCompletion = await source.callCount
        XCTAssertEqual(fetchCountAfterCompletion, 2)

        let cachedMessage = try await registry.waitForCompletion(messageId: 501)
        XCTAssertEqual(cachedMessage.id, 901)
        let fetchCountAfterCacheHit = await source.callCount
        XCTAssertEqual(fetchCountAfterCacheHit, 2)
    }

    func testTerminalFailureIsDeliveredToEveryObserver() async {
        let source = ScriptedMessageStatusSource(
            responses: [.failed("Provider unavailable")]
        )
        let registry = ChatMessageCompletionRegistry(
            fetchStatus: { try await source.fetch(messageId: $0) }
        )

        let firstObserver = Task {
            try await registry.waitForCompletion(messageId: 501)
        }
        let secondObserver = Task {
            try await registry.waitForCompletion(messageId: 501)
        }

        for observer in [firstObserver, secondObserver] {
            do {
                _ = try await observer.value
                XCTFail("Expected terminal processing failure")
            } catch ChatServiceError.processingFailed(let message) {
                XCTAssertEqual(message, "Provider unavailable")
            } catch {
                XCTFail("Unexpected error: \(error)")
            }
        }
        let fetchCount = await source.callCount
        XCTAssertEqual(fetchCount, 1)
    }

    func testAdaptivePolicyTimesOutWithFewerSteadyStateRequests() async {
        let source = AlwaysProcessingMessageStatusSource()
        let delays = CompletionDelayRecorder()
        let policy = ChatMessageCompletionPollingPolicy.adaptive
        let registry = ChatMessageCompletionRegistry(
            fetchStatus: { try await source.fetch(messageId: $0) },
            policy: policy,
            sleep: { delay in await delays.record(delay) }
        )

        do {
            _ = try await registry.waitForCompletion(messageId: 501)
            XCTFail("Expected timeout")
        } catch ChatServiceError.timeout {
            // Expected.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertEqual(policy.maximumRequestCount, 36)
        let fetchCount = await source.callCount
        let recordedDelays = await delays.values
        XCTAssertEqual(fetchCount, 36)
        XCTAssertEqual(recordedDelays.count, 35)
        XCTAssertEqual(recordedDelays.reduce(0, +), 60_000_000_000)
    }

    private static func assistantMessage() -> ChatMessage {
        ChatMessage(
            id: 901,
            role: .assistant,
            timestamp: Date(timeIntervalSince1970: 0),
            content: "Ready",
            status: .completed
        )
    }

    private func waitUntil(
        _ condition: @escaping () async -> Bool,
        timeout: TimeInterval = 1
    ) async -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await condition() {
                return true
            }
            await Task.yield()
        }
        return await condition()
    }
}

private enum ScriptedMessageStatus {
    case processing
    case completed(ChatMessage)
    case failed(String)
}

private actor ScriptedMessageStatusSource {
    private let responses: [ScriptedMessageStatus]
    private let firstFetchGate: CompletionRegistryGate?
    private(set) var callCount = 0

    init(
        responses: [ScriptedMessageStatus],
        firstFetchGate: CompletionRegistryGate? = nil
    ) {
        self.responses = responses
        self.firstFetchGate = firstFetchGate
    }

    func fetch(messageId: Int) async throws -> MessageStatusResponse {
        let responseIndex = callCount
        callCount += 1
        if responseIndex == 0 {
            await firstFetchGate?.wait()
        }
        let response = responses[min(responseIndex, responses.count - 1)]
        switch response {
        case .processing:
            return MessageStatusResponse(messageId: messageId, status: .processing)
        case .completed(let message):
            return MessageStatusResponse(
                messageId: messageId,
                status: .completed,
                assistantMessage: message
            )
        case .failed(let error):
            return MessageStatusResponse(
                messageId: messageId,
                status: .failed,
                error: error
            )
        }
    }
}

private actor AlwaysProcessingMessageStatusSource {
    private(set) var callCount = 0

    func fetch(messageId: Int) async throws -> MessageStatusResponse {
        callCount += 1
        return MessageStatusResponse(messageId: messageId, status: .processing)
    }
}

private actor CompletionRegistryGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !isOpen else { return }
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

private actor ControlledCompletionSleeper {
    private(set) var recordedDelays: [UInt64] = []
    private var waiters: [CheckedContinuation<Void, Error>] = []

    func sleep(nanoseconds: UInt64) async throws {
        recordedDelays.append(nanoseconds)
        try await withCheckedThrowingContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func resumeNext() {
        guard !waiters.isEmpty else { return }
        waiters.removeFirst().resume()
    }
}

private actor CompletionProgressRecorder {
    private(set) var attempts: [Int] = []

    func record(_ attempt: Int) {
        attempts.append(attempt)
    }
}

private actor CompletionDelayRecorder {
    private(set) var values: [UInt64] = []

    func record(_ value: UInt64) {
        values.append(value)
    }
}
