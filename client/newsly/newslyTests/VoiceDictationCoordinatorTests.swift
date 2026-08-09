import Foundation
import XCTest
@testable import newsly

@MainActor
final class VoiceDictationCoordinatorTests: XCTestCase {
    func testStandardAndSurfaceDeadlinesReachProvider() async throws {
        let transcriber = ImmediateSpeechTranscriber(transcript: "unused")
        let standardCoordinator = VoiceDictationCoordinator(transcriber: transcriber)
        let onboardingDeadlines = SpeechRecordingDeadlines(
            noSpeechTimeoutSeconds: 10,
            maximumDurationSeconds: 30
        )
        let onboardingCoordinator = VoiceDictationCoordinator(
            transcriber: transcriber,
            deadlines: onboardingDeadlines
        )

        try await standardCoordinator.start()
        XCTAssertEqual(
            transcriber.requestedDeadlines,
            [.standard]
        )
        XCTAssertEqual(SpeechRecordingDeadlines.standard.noSpeechTimeoutSeconds, 10)
        XCTAssertEqual(SpeechRecordingDeadlines.standard.maximumDurationSeconds, 60)
        standardCoordinator.cancel()

        try await onboardingCoordinator.start()
        XCTAssertEqual(
            transcriber.requestedDeadlines,
            [.standard, onboardingDeadlines]
        )
        onboardingCoordinator.cancel()
    }

    func testCancelDuringStartupReleasesProviderBeforeStartupReturns() async {
        let transcriber = DelayedStartSpeechTranscriber()
        let coordinator = VoiceDictationCoordinator(transcriber: transcriber)

        let startTask = Task { @MainActor in
            try? await coordinator.start()
        }
        let didBegin = await waitUntil { transcriber.isStarting }

        XCTAssertTrue(didBegin)
        XCTAssertTrue(coordinator.hasActiveSession)
        XCTAssertTrue(transcriber.hasActiveSession)

        coordinator.cancel()

        XCTAssertFalse(coordinator.hasActiveSession)
        XCTAssertFalse(transcriber.hasActiveSession)
        XCTAssertEqual(transcriber.cancelCallCount, 1)
        await startTask.value
    }

    func testStopDuringStartupCancelsProviderAndPreventsLateOwnership() async throws {
        let transcriber = DelayedStartSpeechTranscriber()
        let coordinator = VoiceDictationCoordinator(transcriber: transcriber)

        let startTask = Task { @MainActor in
            try? await coordinator.start()
        }
        let didBegin = await waitUntil { transcriber.isStarting }

        XCTAssertTrue(didBegin)
        XCTAssertTrue(coordinator.hasActiveSession)
        XCTAssertTrue(transcriber.hasActiveSession)

        do {
            _ = try await coordinator.stop()
            XCTFail("Stopping before recording starts should fail")
        } catch VoiceDictationError.recordingFailed {
            // The failed stop must still cancel provider ownership.
        } catch {
            XCTFail("Expected recordingFailed, got \(error)")
        }

        await startTask.value
        XCTAssertFalse(coordinator.hasActiveSession)
        XCTAssertFalse(transcriber.hasActiveSession)
        XCTAssertEqual(transcriber.cancelCallCount, 1)

        let replacement = try transcriber.makeSession(deadlines: .standard)
        replacement.cancel()
        XCTAssertFalse(transcriber.hasActiveSession)
        XCTAssertEqual(transcriber.cancelCallCount, 2)
    }

    func testCancelAllowsAnotherSessionImmediately() async throws {
        let transcriber = ImmediateSpeechTranscriber(transcript: "second transcript")
        let firstCoordinator = VoiceDictationCoordinator(transcriber: transcriber)
        let secondCoordinator = VoiceDictationCoordinator(transcriber: transcriber)

        try await firstCoordinator.start()
        XCTAssertTrue(transcriber.hasActiveSession)
        XCTAssertThrowsError(
            try transcriber.makeSession(deadlines: .standard)
        ) { error in
            guard case VoiceDictationError.sessionBusy = error else {
                return XCTFail("Expected sessionBusy, got \(error)")
            }
        }

        firstCoordinator.cancel()
        XCTAssertFalse(transcriber.hasActiveSession)

        try await secondCoordinator.start()
        let transcript = try await secondCoordinator.stop()

        XCTAssertEqual(transcript, "second transcript")
        XCTAssertFalse(secondCoordinator.hasActiveSession)
        XCTAssertFalse(transcriber.hasActiveSession)
    }

    func testCancelledStartupCannotClearOrNotifyReplacementSession() async throws {
        let transcriber = SupersededStartSpeechTranscriber()
        let coordinator = VoiceDictationCoordinator(transcriber: transcriber)
        var firstErrors: [String] = []
        var firstStates: [SpeechTranscriptionState] = []
        var replacementStates: [SpeechTranscriptionState] = []

        let firstStart = Task { @MainActor in
            try await coordinator.start(
                onError: { firstErrors.append($0) },
                onStateChange: { firstStates.append($0) }
            )
        }
        let didBeginFirstStart = await waitUntil { transcriber.isFirstSessionStarting }
        XCTAssertTrue(didBeginFirstStart)

        coordinator.cancel()
        try await coordinator.start(
            onStateChange: { replacementStates.append($0) }
        )
        transcriber.finishFirstStart()

        do {
            try await firstStart.value
            XCTFail("The superseded startup should be cancelled")
        } catch is CancellationError {}

        XCTAssertTrue(coordinator.hasActiveSession)
        XCTAssertTrue(transcriber.hasActiveSession)
        XCTAssertTrue(firstErrors.isEmpty)
        XCTAssertEqual(firstStates, [.starting])
        XCTAssertEqual(replacementStates, [.starting, .recording])

        coordinator.cancel()
    }

    func testAutomaticNoSpeechFailureReleasesCoordinatorAndReportsState() async throws {
        let transcriber = ImmediateSpeechTranscriber(transcript: "unused")
        let coordinator = VoiceDictationCoordinator(transcriber: transcriber)
        var states: [SpeechTranscriptionState] = []
        var errors: [String] = []
        var reasons: [SpeechStopReason] = []
        var wasReleasedWhenFailureBecameVisible = false

        try await coordinator.start(
            onError: { errors.append($0) },
            onStateChange: { state in
                states.append(state)
                if case .failed = state {
                    wasReleasedWhenFailureBecameVisible = !coordinator.hasActiveSession
                }
            },
            onStopReason: { reasons.append($0) }
        )
        transcriber.completeWithoutSpeech()
        let didRelease = await waitUntil { !coordinator.hasActiveSession }

        XCTAssertTrue(didRelease)
        XCTAssertTrue(states.contains(.starting))
        XCTAssertTrue(states.contains(.recording))
        XCTAssertTrue(states.contains(.failed("No speech detected. Try again.")))
        XCTAssertEqual(errors, ["No speech detected. Try again."])
        XCTAssertEqual(reasons, [.noSpeechTimeout])
        XCTAssertTrue(wasReleasedWhenFailureBecameVisible)
        XCTAssertFalse(transcriber.hasActiveSession)
    }

    func testAutomaticCompletionReleasesBeforeDownstreamTranscriptWorkFinishes() async throws {
        let transcriber = ImmediateSpeechTranscriber(transcript: "new interests")
        let coordinator = VoiceDictationCoordinator(transcriber: transcriber)
        let gate = TranscriptDeliveryGate()

        try await coordinator.start(
            onTranscriptFinal: { transcript in
                XCTAssertEqual(transcript, "new interests")
                await gate.wait()
            }
        )
        transcriber.completeAutomatically()

        let didRelease = await waitUntil { !coordinator.hasActiveSession }
        XCTAssertTrue(didRelease)
        XCTAssertTrue(gate.didStartWaiting)
        XCTAssertFalse(transcriber.hasActiveSession)
        gate.open()
    }

    private func waitUntil(
        attempts: Int = 100,
        condition: @escaping @MainActor () -> Bool
    ) async -> Bool {
        for _ in 0..<attempts {
            if condition() { return true }
            await Task.yield()
        }
        return condition()
    }
}

@MainActor
private final class DelayedStartSpeechTranscriber: SpeechTranscribing {
    var isAvailable = true
    private(set) var isStarting = false
    private(set) var hasActiveSession = false
    private(set) var cancelCallCount = 0

    private var activeSessionID: UUID?
    private var startContinuation: CheckedContinuation<Void, Never>?

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        _ = deadlines
        guard activeSessionID == nil else { throw VoiceDictationError.sessionBusy }
        let sessionID = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSessionID = sessionID
        hasActiveSession = true
        return SpeechTranscriptionSession(
            id: sessionID,
            events: pair.stream,
            start: { [weak self] id in
                guard let self, self.activeSessionID == id else {
                    throw VoiceDictationError.noActiveSession
                }
                self.isStarting = true
                await withCheckedContinuation { continuation in
                    self.startContinuation = continuation
                }
                guard self.activeSessionID == id else {
                    throw VoiceDictationError.noActiveSession
                }
            },
            stop: { _ in throw VoiceDictationError.recordingFailed },
            cancel: { [weak self] id in
                guard let self, self.activeSessionID == id else { return }
                self.cancelCallCount += 1
                self.activeSessionID = nil
                self.hasActiveSession = false
                self.startContinuation?.resume()
                self.startContinuation = nil
                pair.continuation.finish()
            }
        )
    }
}

@MainActor
private final class ImmediateSpeechTranscriber: SpeechTranscribing {
    var isAvailable = true
    private(set) var hasActiveSession = false
    private(set) var requestedDeadlines: [SpeechRecordingDeadlines] = []

    private let transcript: String
    private var activeSessionID: UUID?
    private var continuation: AsyncStream<SpeechTranscriptionEvent>.Continuation?

    init(transcript: String) {
        self.transcript = transcript
    }

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        guard activeSessionID == nil else { throw VoiceDictationError.sessionBusy }
        requestedDeadlines.append(deadlines)
        let sessionID = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSessionID = sessionID
        continuation = pair.continuation
        hasActiveSession = true
        return SpeechTranscriptionSession(
            id: sessionID,
            events: pair.stream,
            start: { [weak self] id in
                guard let self, self.activeSessionID == id else {
                    throw VoiceDictationError.noActiveSession
                }
                self.continuation?.yield(.stateChange(.recording))
            },
            stop: { [weak self] id in
                guard let self, self.activeSessionID == id else {
                    throw VoiceDictationError.noActiveSession
                }
                let transcript = self.transcript
                self.release()
                return transcript
            },
            cancel: { [weak self] id in
                guard self?.activeSessionID == id else { return }
                self?.release()
            }
        )
    }

    func completeWithoutSpeech() {
        let message = "No speech detected. Try again."
        continuation?.yield(.stateChange(.failed(message)))
        continuation?.yield(.error(message))
        continuation?.yield(.stopReason(.noSpeechTimeout))
        release()
    }

    func completeAutomatically() {
        continuation?.yield(.stateChange(.transcribing))
        continuation?.yield(.transcriptFinal(transcript))
        continuation?.yield(.stateChange(.idle))
        continuation?.yield(.stopReason(.silenceAutoStop))
        release()
    }

    private func release() {
        activeSessionID = nil
        hasActiveSession = false
        continuation?.finish()
        continuation = nil
    }
}

@MainActor
private final class SupersededStartSpeechTranscriber: SpeechTranscribing {
    var isAvailable = true
    private(set) var isFirstSessionStarting = false
    private(set) var hasActiveSession = false

    private var makeSessionCount = 0
    private var activeSessionID: UUID?
    private var firstStartContinuation: CheckedContinuation<Void, Never>?

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        _ = deadlines
        guard activeSessionID == nil else { throw VoiceDictationError.sessionBusy }
        makeSessionCount += 1
        let isFirstSession = makeSessionCount == 1
        let sessionID = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSessionID = sessionID
        hasActiveSession = true

        return SpeechTranscriptionSession(
            id: sessionID,
            events: pair.stream,
            start: { [weak self] id in
                guard let self else { throw VoiceDictationError.noActiveSession }
                if isFirstSession {
                    self.isFirstSessionStarting = true
                    await withCheckedContinuation { continuation in
                        self.firstStartContinuation = continuation
                    }
                }
                guard self.activeSessionID == id else {
                    throw VoiceDictationError.noActiveSession
                }
            },
            stop: { _ in throw VoiceDictationError.recordingFailed },
            cancel: { [weak self] id in
                guard self?.activeSessionID == id else { return }
                self?.activeSessionID = nil
                self?.hasActiveSession = false
                pair.continuation.finish()
            }
        )
    }

    func finishFirstStart() {
        firstStartContinuation?.resume()
        firstStartContinuation = nil
    }
}

@MainActor
private final class TranscriptDeliveryGate {
    private(set) var didStartWaiting = false
    private var continuation: CheckedContinuation<Void, Never>?

    func wait() async {
        didStartWaiting = true
        await withCheckedContinuation { continuation in
            self.continuation = continuation
        }
    }

    func open() {
        continuation?.resume()
        continuation = nil
    }
}
