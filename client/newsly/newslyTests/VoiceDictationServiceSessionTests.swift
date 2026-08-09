import AVFoundation
import XCTest
@testable import newsly

@MainActor
final class VoiceDictationServiceSessionTests: XCTestCase {
    func testManualStopJoinsAutomaticFinalizationWithoutLosingTranscript() async throws {
        let transcriber = SlowFirstVoiceTranscriber()
        let service = VoiceDictationService { _ in
            await transcriber.transcribe()
        }
        let session = try service.makeSession(deadlines: .standard)
        let recorder = try makeRecorder()
        try service.prepareRecordingForTesting(sessionID: session.id, recorder: recorder)

        service.triggerAutomaticStopForTesting(reason: .silenceAutoStop)
        await transcriber.waitUntilFirstStarted()

        let manualStop = Task { @MainActor in
            try await session.stop()
        }
        await Task.yield()
        await transcriber.finishFirst()

        let transcript = try await manualStop.value
        XCTAssertEqual(transcript, "stale transcript")

        var replacementSession: SpeechTranscriptionSession?
        for _ in 0..<100 where replacementSession == nil {
            replacementSession = try? service.makeSession(deadlines: .standard)
            if replacementSession == nil {
                await Task.yield()
            }
        }
        let replacement = try XCTUnwrap(replacementSession)
        replacement.cancel()
    }

    func testConcreteServiceReservesAndSynchronouslyReleasesSessionWithoutUsingMicrophone() throws {
        let service = VoiceDictationService.shared
        let firstSession = try service.makeSession(deadlines: .standard)

        XCTAssertThrowsError(
            try service.makeSession(deadlines: .standard)
        ) { error in
            guard case VoiceDictationError.sessionBusy = error else {
                return XCTFail("Expected sessionBusy, got \(error)")
            }
        }

        firstSession.cancel()

        let replacementSession = try service.makeSession(deadlines: .standard)
        replacementSession.cancel()
    }

    func testCancelDuringSlowTranscriptionDoesNotTearDownImmediateReplacement() async throws {
        let transcriber = SlowFirstVoiceTranscriber()
        let service = VoiceDictationService { _ in
            await transcriber.transcribe()
        }
        let firstSession = try service.makeSession(deadlines: .standard)
        let firstRecorder = try makeRecorder()
        try service.prepareRecordingForTesting(
            sessionID: firstSession.id,
            recorder: firstRecorder
        )

        let firstStopTask = Task { @MainActor in
            try await firstSession.stop()
        }
        await transcriber.waitUntilFirstStarted()
        firstSession.cancel()

        let replacementSession = try service.makeSession(deadlines: .standard)
        let replacementRecorder = try makeRecorder()
        try service.prepareRecordingForTesting(
            sessionID: replacementSession.id,
            recorder: replacementRecorder
        )
        let replacementURL = replacementRecorder.url
        XCTAssertTrue(FileManager.default.fileExists(atPath: replacementURL.path))

        await transcriber.finishFirst()
        do {
            _ = try await firstStopTask.value
            XCTFail("Cancelled first session should not complete successfully")
        } catch is CancellationError {
            // The released session converts its stale completion into cancellation.
        }

        XCTAssertTrue(FileManager.default.fileExists(atPath: replacementURL.path))
        let transcript = try await replacementSession.stop()
        XCTAssertEqual(transcript, "replacement transcript")
        XCTAssertFalse(FileManager.default.fileExists(atPath: replacementURL.path))
    }

    func testStaleRecorderCallbacksDoNotTerminateReplacement() async throws {
        let service = VoiceDictationService { _ in "transcript" }
        let firstSession = try service.makeSession(deadlines: .standard)
        let firstRecorder = try makeRecorder()
        try service.prepareRecordingForTesting(
            sessionID: firstSession.id,
            recorder: firstRecorder
        )
        firstSession.cancel()

        let replacementSession = try service.makeSession(deadlines: .standard)
        let replacementRecorder = try makeRecorder()
        try service.prepareRecordingForTesting(
            sessionID: replacementSession.id,
            recorder: replacementRecorder
        )

        service.audioRecorderDidFinishRecording(firstRecorder, successfully: false)
        service.audioRecorderEncodeErrorDidOccur(
            firstRecorder,
            error: TestRecorderError.encoding
        )
        await drainDelegateCallbacks()

        XCTAssertThrowsError(
            try service.makeSession(deadlines: .standard)
        ) { error in
            guard case VoiceDictationError.sessionBusy = error else {
                return XCTFail("Expected sessionBusy, got \(error)")
            }
        }
        replacementSession.cancel()
    }

    func testCurrentRecorderUnsuccessfulFinishTerminatesOwnership() async throws {
        let service = VoiceDictationService { _ in "transcript" }
        let session = try service.makeSession(deadlines: .standard)
        let recorder = try makeRecorder()
        try service.prepareRecordingForTesting(sessionID: session.id, recorder: recorder)

        service.audioRecorderDidFinishRecording(recorder, successfully: false)
        await drainDelegateCallbacks()

        let replacementSession = try service.makeSession(deadlines: .standard)
        replacementSession.cancel()
    }

    func testCurrentRecorderEncodeErrorTerminatesOwnership() async throws {
        let service = VoiceDictationService { _ in "transcript" }
        let session = try service.makeSession(deadlines: .standard)
        let recorder = try makeRecorder()
        try service.prepareRecordingForTesting(sessionID: session.id, recorder: recorder)

        service.audioRecorderEncodeErrorDidOccur(
            recorder,
            error: TestRecorderError.encoding
        )
        await drainDelegateCallbacks()

        let replacementSession = try service.makeSession(deadlines: .standard)
        replacementSession.cancel()
    }

    func testRouteLossAfterCaptureStopsDoesNotCancelBlockedTranscription() async throws {
        let transcriber = SlowFirstVoiceTranscriber()
        let service = VoiceDictationService { _ in
            await transcriber.transcribe()
        }
        let session = try service.makeSession(deadlines: .standard)
        let recorder = try makeRecorder()
        try service.prepareRecordingForTesting(sessionID: session.id, recorder: recorder)

        let stopTask = Task { @MainActor in
            try await session.stop()
        }
        await transcriber.waitUntilFirstStarted()
        NotificationCenter.default.post(
            name: AVAudioSession.routeChangeNotification,
            object: nil,
            userInfo: [
                AVAudioSessionRouteChangeReasonKey:
                    AVAudioSession.RouteChangeReason.noSuitableRouteForCategory.rawValue
            ]
        )
        await drainDelegateCallbacks()

        XCTAssertThrowsError(try service.makeSession(deadlines: .standard)) { error in
            guard case VoiceDictationError.sessionBusy = error else {
                return XCTFail("Expected sessionBusy, got \(error)")
            }
        }

        await transcriber.finishFirst()
        let transcript = try await stopTask.value
        XCTAssertEqual(transcript, "stale transcript")
        let replacementSession = try service.makeSession(deadlines: .standard)
        replacementSession.cancel()
    }

    private func makeRecorder() throws -> AVAudioRecorder {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(
            "voice-dictation-test-\(UUID().uuidString).m4a"
        )
        let recorder = try AVAudioRecorder(
            url: url,
            settings: [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: 16_000.0,
                AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
            ]
        )
        try Data("test audio".utf8).write(to: url)
        return recorder
    }

    private func drainDelegateCallbacks() async {
        for _ in 0..<5 {
            await Task.yield()
        }
    }
}

private enum TestRecorderError: Error {
    case encoding
}

private actor SlowFirstVoiceTranscriber {
    private var invocationCount = 0
    private var firstStarted = false
    private var startWaiters: [CheckedContinuation<Void, Never>] = []
    private var firstContinuation: CheckedContinuation<String, Never>?

    func transcribe() async -> String {
        invocationCount += 1
        guard invocationCount == 1 else { return "replacement transcript" }

        firstStarted = true
        let waiters = startWaiters
        startWaiters.removeAll()
        waiters.forEach { $0.resume() }
        return await withCheckedContinuation { continuation in
            firstContinuation = continuation
        }
    }

    func waitUntilFirstStarted() async {
        guard !firstStarted else { return }
        await withCheckedContinuation { continuation in
            startWaiters.append(continuation)
        }
    }

    func finishFirst() {
        firstContinuation?.resume(returning: "stale transcript")
        firstContinuation = nil
    }
}
