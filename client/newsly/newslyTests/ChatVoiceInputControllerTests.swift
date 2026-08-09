import XCTest
@testable import newsly

@MainActor
final class ChatVoiceInputControllerTests: XCTestCase {
    func testNoSpeechTimeoutReportsOneTerminalError() async {
        let transcriber = NoSpeechChatTranscriber()
        let controller = ChatVoiceInputController(
            transcriptionService: transcriber,
            authService: AuthenticationService.shared,
            tokenStore: KeychainManager.shared,
            refreshAvailability: { true },
            setBackendAvailability: { _ in },
            initiallyAvailable: true
        )
        var errors: [String] = []
        controller.configure(
            onTranscriptReady: { _ in },
            onError: { errors.append($0) }
        )

        await controller.start()
        transcriber.emitNoSpeechTimeout()
        for _ in 0..<20 {
            await Task.yield()
        }

        XCTAssertEqual(errors, ["No speech detected. Try again."])
        XCTAssertFalse(controller.isRecording)
        XCTAssertFalse(controller.isTranscribing)
        XCTAssertEqual(controller.state, .failed("No speech detected. Try again."))
    }
}

@MainActor
private final class NoSpeechChatTranscriber: SpeechTranscribing {
    var isAvailable = true

    private var activeSessionID: UUID?
    private var continuation: AsyncStream<SpeechTranscriptionEvent>.Continuation?

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
                guard self?.activeSessionID == id else {
                    throw VoiceDictationError.noActiveSession
                }
            },
            stop: { _ in throw VoiceDictationError.recordingFailed },
            cancel: { [weak self] id in self?.release(id) }
        )
    }

    func emitNoSpeechTimeout() {
        guard let sessionID = activeSessionID, let continuation else { return }
        let message = "No speech detected. Try again."
        continuation.yield(.stateChange(.failed(message)))
        continuation.yield(.error(message))
        continuation.yield(.stopReason(.noSpeechTimeout))
        release(sessionID)
    }

    private func release(_ sessionID: UUID) {
        guard activeSessionID == sessionID else { return }
        activeSessionID = nil
        continuation?.finish()
        continuation = nil
    }
}
