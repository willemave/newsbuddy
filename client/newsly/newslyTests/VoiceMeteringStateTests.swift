import XCTest
@testable import newsly

final class VoiceMeteringStateTests: XCTestCase {
    func testSpeechOnFirstSampleIsDetectedBeforeCalibrationCanRaiseThreshold() {
        var state = VoiceMeteringState()

        let action = state.observe(
            powerDb: -20,
            recordingDuration: 0.1,
            deadlines: .standard
        )

        XCTAssertEqual(action, .none)
        XCTAssertTrue(state.hasDetectedSpeech)
        XCTAssertEqual(state.speechThresholdDb, -42, accuracy: 0.001)
    }

    func testCalibrationFreezesAfterImmediateSpeech() {
        var state = VoiceMeteringState()
        _ = state.observe(powerDb: -20, recordingDuration: 0.1, deadlines: .standard)

        let action = state.observe(
            powerDb: -45,
            recordingDuration: 0.2,
            deadlines: .standard
        )

        XCTAssertEqual(action, .none)
        XCTAssertTrue(state.hasDetectedSpeech)
        XCTAssertEqual(state.speechThresholdDb, -42, accuracy: 0.001)
    }

    func testQuietCalibrationPreservesAdaptiveThresholdMath() {
        var state = VoiceMeteringState()

        _ = state.observe(powerDb: -60, recordingDuration: 0.1, deadlines: .standard)
        XCTAssertEqual(state.speechThresholdDb, -42, accuracy: 0.001)

        _ = state.observe(powerDb: -52, recordingDuration: 0.2, deadlines: .standard)
        XCTAssertEqual(state.speechThresholdDb, -40, accuracy: 0.001)

        _ = state.observe(powerDb: -37, recordingDuration: 0.4, deadlines: .standard)
        XCTAssertTrue(state.hasDetectedSpeech)
    }

    func testNoSpeechStopsAtSurfaceDeadline() {
        var state = VoiceMeteringState()

        XCTAssertEqual(
            state.observe(powerDb: -60, recordingDuration: 9.99, deadlines: .standard),
            .none
        )
        XCTAssertEqual(
            state.observe(powerDb: -60, recordingDuration: 10, deadlines: .standard),
            .noSpeechTimeout
        )
    }

    func testFourSecondsOfSilenceAfterSpeechAutoStops() {
        var state = VoiceMeteringState()
        _ = state.observe(powerDb: -20, recordingDuration: 0.1, deadlines: .standard)
        _ = state.observe(powerDb: -60, recordingDuration: 0.2, deadlines: .standard)

        XCTAssertEqual(
            state.observe(powerDb: -60, recordingDuration: 4.19, deadlines: .standard),
            .none
        )
        XCTAssertEqual(
            state.observe(powerDb: -60, recordingDuration: 4.21, deadlines: .standard),
            .automaticStop(.silenceAutoStop)
        )
    }

    func testMaximumDurationWinsWhenDeadlinesCoincide() {
        let equalDeadlines = SpeechRecordingDeadlines(
            noSpeechTimeoutSeconds: 10,
            maximumDurationSeconds: 10
        )
        var noSpeechState = VoiceMeteringState()

        XCTAssertEqual(
            noSpeechState.observe(
                powerDb: -60,
                recordingDuration: 10,
                deadlines: equalDeadlines
            ),
            .automaticStop(.maximumDuration)
        )

        var speechState = VoiceMeteringState()
        _ = speechState.observe(powerDb: -20, recordingDuration: 0.1, deadlines: equalDeadlines)
        _ = speechState.observe(powerDb: -60, recordingDuration: 0.2, deadlines: equalDeadlines)
        XCTAssertEqual(
            speechState.observe(
                powerDb: -60,
                recordingDuration: 10,
                deadlines: equalDeadlines
            ),
            .automaticStop(.maximumDuration)
        )
    }
}
