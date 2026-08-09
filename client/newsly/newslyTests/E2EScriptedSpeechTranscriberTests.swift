import Foundation
import XCTest
@testable import newsly

@MainActor
final class E2EScriptedSpeechTranscriberTests: XCTestCase {
    func testSuccessAndEmptyManualScenarios() async throws {
        let success = E2EScriptedSpeechTranscriber(
            scenario: .success,
            transcript: "spoken adjustment"
        )
        let successSession = try success.makeSession(deadlines: .standard)
        try await successSession.start()
        let successTranscript = try await successSession.stop()
        XCTAssertEqual(successTranscript, "spoken adjustment")

        let empty = E2EScriptedSpeechTranscriber(
            scenario: .emptyTranscript,
            transcript: "ignored"
        )
        let emptySession = try empty.makeSession(deadlines: .standard)
        try await emptySession.start()
        let emptyTranscript = try await emptySession.stop()
        XCTAssertEqual(emptyTranscript, "")
    }

    func testStartAndTranscriptionFailureScenariosReleaseOwnership() async throws {
        let startFailure = E2EScriptedSpeechTranscriber(
            scenario: .startFailure,
            transcript: "ignored"
        )
        let failedStartSession = try startFailure.makeSession(deadlines: .standard)
        do {
            try await failedStartSession.start()
            XCTFail("Expected scripted start failure")
        } catch VoiceDictationError.recordingFailed {
            // Expected.
        }
        let replacementSession = try startFailure.makeSession(deadlines: .standard)
        replacementSession.cancel()

        let transcriptionFailure = E2EScriptedSpeechTranscriber(
            scenario: .transcriptionFailure,
            transcript: "ignored"
        )
        let failedTranscriptionSession = try transcriptionFailure.makeSession(deadlines: .standard)
        try await failedTranscriptionSession.start()
        do {
            _ = try await failedTranscriptionSession.stop()
            XCTFail("Expected scripted transcription failure")
        } catch VoiceDictationError.transcriptionFailed {
            // Expected.
        }
        let replacementAfterTranscriptionFailure = try transcriptionFailure.makeSession(
            deadlines: .standard
        )
        replacementAfterTranscriptionFailure.cancel()
    }

    func testAllAutomaticTerminalScenarios() async throws {
        let silenceEvents = try await automaticEvents(for: .silenceAutoStop)
        XCTAssertTrue(silenceEvents.contains(.transcriptFinal("scripted transcript")))
        XCTAssertTrue(silenceEvents.contains(.stopReason(.silenceAutoStop)))

        let maximumEvents = try await automaticEvents(for: .maximumDuration)
        XCTAssertTrue(maximumEvents.contains(.transcriptFinal("scripted transcript")))
        XCTAssertTrue(maximumEvents.contains(.stopReason(.maximumDuration)))

        let noSpeechEvents = try await automaticEvents(for: .noSpeechTimeout)
        XCTAssertTrue(noSpeechEvents.contains(.stateChange(.failed("No speech detected. Try again."))))
        XCTAssertTrue(noSpeechEvents.contains(.error("No speech detected. Try again.")))
        XCTAssertTrue(noSpeechEvents.contains(.stopReason(.noSpeechTimeout)))
        XCTAssertFalse(noSpeechEvents.contains(.transcriptFinal("scripted transcript")))
    }

    func testBackgroundNotificationCancelsActiveScriptedSession() async throws {
        let transcriber = E2EScriptedSpeechTranscriber(
            scenario: .success,
            transcript: "ignored"
        )
        let session = try transcriber.makeSession(deadlines: .standard)
        try await session.start()

        NotificationCenter.default.post(
            name: speechAppDidEnterBackgroundNotification,
            object: nil
        )
        let events = await collectEvents(from: session)

        XCTAssertTrue(events.contains(.stateChange(.idle)))
        XCTAssertTrue(events.contains(.stopReason(.cancel)))
        let replacementSession = try transcriber.makeSession(deadlines: .standard)
        replacementSession.cancel()
    }

    private func automaticEvents(
        for scenario: E2ESpeechScenario
    ) async throws -> [SpeechTranscriptionEvent] {
        let transcriber = E2EScriptedSpeechTranscriber(
            scenario: scenario,
            transcript: "scripted transcript"
        )
        let session = try transcriber.makeSession(deadlines: .standard)
        try await session.start()
        return await collectEvents(from: session)
    }

    private func collectEvents(
        from session: SpeechTranscriptionSession
    ) async -> [SpeechTranscriptionEvent] {
        var events: [SpeechTranscriptionEvent] = []
        for await event in session.events {
            events.append(event)
        }
        return events
    }
}
