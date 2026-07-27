import XCTest
@testable import newsly

final class LearningDeckStatusRegistryTests: XCTestCase {
    func testSimultaneousObserversShareOneFetchSequence() async throws {
        let firstFetchGate = LearningDeckRegistryGate()
        let source = ScriptedLearningDeckStatusSource(
            decks: [
                makeRegistryDeck(status: .preparing),
                makeRegistryDeck(status: .completed, viewerAvailable: true),
            ],
            firstFetchGate: firstFetchGate
        )
        let sleeper = ControlledLearningDeckSleeper()
        let registry = LearningDeckStatusRegistry(
            fetchDeck: { try await source.fetch(deckId: $0) },
            policy: LearningDeckStatusPollingPolicy(delaysNanoseconds: [1]),
            sleep: { try await sleeper.sleep(nanoseconds: $0) }
        )

        let firstObserver = Task {
            try await registry.waitUntilTerminal(deckId: 41)
        }
        let didStartFirstFetch = await waitForDeckRegistryCondition {
            await source.callCount == 1
        }
        XCTAssertTrue(didStartFirstFetch)

        let secondObserver = Task {
            try await registry.waitUntilTerminal(deckId: 41)
        }
        let didAttachBothObservers = await waitForDeckRegistryCondition {
            await registry.activeObserverCount(deckId: 41) == 2
        }
        XCTAssertTrue(didAttachBothObservers)

        await firstFetchGate.open()
        let didReachFirstDelay = await waitForDeckRegistryCondition {
            await sleeper.recordedDelays.count == 1
        }
        XCTAssertTrue(didReachFirstDelay)
        await sleeper.resumeNext()

        let firstDeck = try await firstObserver.value
        let secondDeck = try await secondObserver.value
        XCTAssertTrue(firstDeck.viewerAvailable)
        XCTAssertEqual(firstDeck.id, secondDeck.id)
        let sharedFetchCount = await source.callCount
        XCTAssertEqual(sharedFetchCount, 2)
    }

    func testObserverCancellationHandsPollingToReplacementObserver() async throws {
        let source = ScriptedLearningDeckStatusSource(
            decks: [
                makeRegistryDeck(status: .generating),
                makeRegistryDeck(status: .completed, viewerAvailable: true),
            ]
        )
        let sleeper = ControlledLearningDeckSleeper()
        let registry = LearningDeckStatusRegistry(
            fetchDeck: { try await source.fetch(deckId: $0) },
            policy: LearningDeckStatusPollingPolicy(delaysNanoseconds: [1]),
            sleep: { try await sleeper.sleep(nanoseconds: $0) },
            orphanGraceNanoseconds: 1_000_000_000
        )

        let firstObserver = Task {
            try await registry.waitUntilTerminal(deckId: 41)
        }
        let didReachDelay = await waitForDeckRegistryCondition {
            await sleeper.recordedDelays.count == 1
        }
        XCTAssertTrue(didReachDelay)

        firstObserver.cancel()
        do {
            _ = try await firstObserver.value
            XCTFail("Expected the first observer to be cancelled")
        } catch is CancellationError {
            // The shared request sequence remains alive briefly for ownership handoff.
        }

        let replacementObserver = Task {
            try await registry.waitUntilTerminal(deckId: 41)
        }
        let didAttachReplacement = await waitForDeckRegistryCondition {
            await registry.activeObserverCount(deckId: 41) == 1
        }
        XCTAssertTrue(didAttachReplacement)
        await sleeper.resumeNext()

        let readyDeck = try await replacementObserver.value
        XCTAssertTrue(readyDeck.viewerAvailable)
        let handoffFetchCount = await source.callCount
        XCTAssertEqual(handoffFetchCount, 2)

        _ = try await registry.waitUntilTerminal(deckId: 41)
        let refetchedCount = await source.callCount
        XCTAssertEqual(
            refetchedCount,
            3,
            "Terminal status must be fetched again because regeneration reuses deck IDs"
        )
    }

    func testAdaptivePolicyPreservesWindowWithFewerRequests() async {
        let source = AlwaysActiveLearningDeckStatusSource()
        let delays = LearningDeckDelayRecorder()
        let policy = LearningDeckStatusPollingPolicy.adaptive
        let registry = LearningDeckStatusRegistry(
            fetchDeck: { await source.fetch(deckId: $0) },
            policy: policy,
            sleep: { delay in await delays.record(delay) }
        )

        do {
            _ = try await registry.waitUntilTerminal(deckId: 41)
            XCTFail("Expected status polling to time out")
        } catch LearningDeckStatusRegistryError.timeout {
            // Expected.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertEqual(policy.maximumRequestCount, 80)
        let fetchCount = await source.callCount
        let recordedDelays = await delays.values
        XCTAssertEqual(fetchCount, 80)
        XCTAssertEqual(recordedDelays.count, 79)
        XCTAssertEqual(recordedDelays.reduce(0, +), 363_000_000_000)
    }

    func testSleepFailureFinishesObserversInsteadOfLeavingThemSuspended() async {
        let registry = LearningDeckStatusRegistry(
            fetchDeck: { deckId in
                makeRegistryDeck(id: deckId, status: .generating)
            },
            policy: LearningDeckStatusPollingPolicy(delaysNanoseconds: [1]),
            sleep: { _ in throw LearningDeckRegistryTestError.sleepFailed }
        )

        do {
            _ = try await registry.waitUntilTerminal(deckId: 41)
            XCTFail("Expected the sleep failure")
        } catch LearningDeckRegistryTestError.sleepFailed {
            // Expected.
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }
}

private enum LearningDeckRegistryTestError: Error {
    case sleepFailed
}

private actor ScriptedLearningDeckStatusSource {
    private let decks: [LearningDeck]
    private let firstFetchGate: LearningDeckRegistryGate?
    private(set) var callCount = 0

    init(
        decks: [LearningDeck],
        firstFetchGate: LearningDeckRegistryGate? = nil
    ) {
        self.decks = decks
        self.firstFetchGate = firstFetchGate
    }

    func fetch(deckId: Int) async throws -> LearningDeck {
        let responseIndex = callCount
        callCount += 1
        if responseIndex == 0 {
            await firstFetchGate?.wait()
        }
        return decks[min(responseIndex, decks.count - 1)]
    }
}

private actor AlwaysActiveLearningDeckStatusSource {
    private(set) var callCount = 0

    func fetch(deckId: Int) -> LearningDeck {
        callCount += 1
        return makeRegistryDeck(id: deckId, status: .generating)
    }
}

private actor LearningDeckRegistryGate {
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
        continuations.forEach { $0.resume() }
    }
}

private actor ControlledLearningDeckSleeper {
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

private actor LearningDeckDelayRecorder {
    private(set) var values: [UInt64] = []

    func record(_ value: UInt64) {
        values.append(value)
    }
}

private func makeRegistryDeck(
    id: Int = 41,
    status: LearningDeckRunStatus,
    viewerAvailable: Bool = false
) -> LearningDeck {
    let timestamp = Date(timeIntervalSince1970: 0)
    return LearningDeck(
        id: id,
        title: "Deck",
        sourceKind: .content,
        sourceURL: nil,
        sourceContentId: 7,
        sourceTitle: "Source",
        sourceMetadata: [:],
        status: status,
        shareEnabled: false,
        viewerAvailable: viewerAvailable,
        sourceNotesAvailable: false,
        latestSuccessfulRunId: viewerAvailable ? 2 : nil,
        latestRun: LearningDeckRun(
            id: 2,
            status: status,
            interestsPrompt: nil,
            timeline: [],
            errorMessage: nil,
            startedAt: timestamp,
            completedAt: status.isActive ? nil : timestamp,
            createdAt: timestamp,
            updatedAt: timestamp
        ),
        createdAt: timestamp,
        updatedAt: timestamp
    )
}

private func waitForDeckRegistryCondition(
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
