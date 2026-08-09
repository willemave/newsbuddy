import Foundation
import XCTest
@testable import newsly

@MainActor
final class LearningDeckFocusRecorderTests: XCTestCase {
    func testManualStopDeliversTrimmedTranscriptAndUsesStandardDeadlines() async {
        let transcriber = FocusSpeechTranscriber(transcript: "  deck focus  ")
        let recorder = makeRecorder(transcriber: transcriber)
        var transcripts: [String] = []

        await recorder.toggleRecording { transcripts.append($0) }

        XCTAssertTrue(recorder.isRecording)
        XCTAssertEqual(transcriber.requestedDeadlines, [.standard])

        await recorder.toggleRecording { transcripts.append($0) }

        XCTAssertEqual(transcripts, ["deck focus"])
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertFalse(recorder.isRecording)
        XCTAssertFalse(recorder.isTranscribing)
        XCTAssertNil(recorder.errorMessage)
        XCTAssertFalse(transcriber.hasActiveSession)
    }

    func testAutomaticSilenceAndMaximumStopsDeliverTranscript() async throws {
        try await assertAutomaticCompletion(reason: .silenceAutoStop)
        try await assertAutomaticCompletion(reason: .maximumDuration)
    }

    func testNoSpeechFailureIsRetryableAndDoesNotDeliverTranscript() async {
        let transcriber = FocusSpeechTranscriber(transcript: "unused")
        let recorder = makeRecorder(transcriber: transcriber)
        var transcripts: [String] = []

        await recorder.toggleRecording { transcripts.append($0) }
        transcriber.completeWithoutSpeech()
        let didFail = await waitUntil { recorder.errorMessage != nil }

        XCTAssertTrue(didFail)
        XCTAssertEqual(recorder.state, .failed("No speech detected. Try again."))
        XCTAssertEqual(recorder.errorMessage, "No speech detected. Try again.")
        XCTAssertTrue(transcripts.isEmpty)
        XCTAssertFalse(transcriber.hasActiveSession)

        var didRestart = false
        for _ in 0..<100 {
            await Task.yield()
            if !recorder.isRecording {
                await recorder.toggleRecording { transcripts.append($0) }
            }
            if recorder.isRecording {
                didRestart = true
                break
            }
        }
        XCTAssertTrue(didRestart)
        recorder.cancelRecording()
    }

    func testCancelReleasesSessionAndResetsState() async {
        let transcriber = FocusSpeechTranscriber(transcript: "unused")
        let recorder = makeRecorder(transcriber: transcriber)

        await recorder.toggleRecording { _ in }
        recorder.cancelRecording()

        XCTAssertFalse(transcriber.hasActiveSession)
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertFalse(recorder.isRecording)
        XCTAssertFalse(recorder.isTranscribing)
        XCTAssertFalse(recorder.isVoiceActionInFlight)
        XCTAssertNil(recorder.errorMessage)
    }

    private func assertAutomaticCompletion(reason: SpeechStopReason) async throws {
        let transcriber = FocusSpeechTranscriber(transcript: "automatic focus")
        let recorder = makeRecorder(transcriber: transcriber)
        var transcripts: [String] = []

        await recorder.toggleRecording { transcripts.append($0) }
        transcriber.completeAutomatically(reason: reason)
        let didComplete = await waitUntil { transcripts == ["automatic focus"] }

        XCTAssertTrue(didComplete)
        XCTAssertEqual(recorder.state, .idle)
        XCTAssertNil(recorder.errorMessage)
        XCTAssertFalse(transcriber.hasActiveSession)
    }

    private func makeRecorder(
        transcriber: FocusSpeechTranscriber
    ) -> LearningDeckFocusRecorder {
        LearningDeckFocusRecorder(
            transcriptionService: transcriber,
            refreshTranscriptionAvailability: { true }
        )
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
private final class FocusSpeechTranscriber: SpeechTranscribing {
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
        let sessionID = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSessionID = sessionID
        continuation = pair.continuation
        hasActiveSession = true
        requestedDeadlines.append(deadlines)

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

    func completeAutomatically(reason: SpeechStopReason) {
        continuation?.yield(.stateChange(.transcribing))
        continuation?.yield(.transcriptFinal(transcript))
        continuation?.yield(.stateChange(.idle))
        continuation?.yield(.stopReason(reason))
        release()
    }

    func completeWithoutSpeech() {
        let message = "No speech detected. Try again."
        continuation?.yield(.stateChange(.failed(message)))
        continuation?.yield(.error(message))
        continuation?.yield(.stopReason(.noSpeechTimeout))
        release()
    }

    private func release() {
        activeSessionID = nil
        hasActiveSession = false
        continuation?.finish()
        continuation = nil
    }
}
