import Foundation
import XCTest
@testable import newsly

@MainActor
final class ChatSessionViewModelTests: XCTestCase {
    func testDefaultChatDictationUsesRecordThenTranscribeService() {
        let viewModel = ChatSessionViewModel(sessionId: 42)
        let mirror = Mirror(reflecting: viewModel)
        let service = mirror.children.first { $0.label == "transcriptionService" }?.value as AnyObject?

        XCTAssertTrue(service === VoiceDictationService.shared)
    }

    func testToggleVoiceRecordingStartsRecordingOnFirstTap() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Ignored")
        let viewModel = ChatSessionViewModel(
            sessionId: 42,
            initialVoiceDictationAvailable: true,
            transcriptionService: transcriptionService
        )

        await viewModel.toggleVoiceRecording()

        XCTAssertTrue(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
    }

    func testToggleVoiceRecordingStopsRecordingOnSecondTapAndPopulatesDraft() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let viewModel = ChatSessionViewModel(
            sessionId: 42,
            initialVoiceDictationAvailable: true,
            transcriptionService: transcriptionService
        )

        await viewModel.toggleVoiceRecording()
        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(viewModel.inputText, "Final transcript")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.startCallCount, 1)
        XCTAssertEqual(transcriptionService.stopCallCount, 1)
    }

    func testToggleVoiceRecordingIgnoresTapWhileTranscribing() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Ignored")
        let viewModel = ChatSessionViewModel(
            sessionId: 42,
            initialVoiceDictationAvailable: true,
            transcriptionService: transcriptionService
        )

        viewModel.isTranscribing = true

        await viewModel.toggleVoiceRecording()

        XCTAssertEqual(transcriptionService.startCallCount, 0)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertTrue(viewModel.isTranscribing)
    }

    func testStopVoiceRecordingPopulatesInputWithoutStreamingPreview() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Final transcript")
        let viewModel = ChatSessionViewModel(
            sessionId: 42,
            initialVoiceDictationAvailable: true,
            transcriptionService: transcriptionService
        )

        viewModel.isRecording = true
        XCTAssertEqual(viewModel.inputText, "")

        await viewModel.stopVoiceRecording()

        XCTAssertEqual(viewModel.inputText, "Final transcript")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertTrue(viewModel.allMessages.isEmpty)
    }

    func testStopVoiceRecordingAppendsToExistingDraft() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "second thought")
        let viewModel = ChatSessionViewModel(
            sessionId: 42,
            initialVoiceDictationAvailable: true,
            transcriptionService: transcriptionService
        )

        viewModel.inputText = "First draft"
        viewModel.isRecording = true

        await viewModel.stopVoiceRecording()

        XCTAssertEqual(viewModel.inputText, "First draft second thought")
    }

    func testSilenceAutoStopPopulatesDraftWithoutManualStop() async {
        let transcriptionService = MockChatSpeechTranscriber(transcript: "Auto transcript")
        let viewModel = ChatSessionViewModel(
            sessionId: 42,
            initialVoiceDictationAvailable: true,
            transcriptionService: transcriptionService
        )

        await viewModel.startVoiceRecording()
        await transcriptionService.simulateSilenceAutoStop()

        XCTAssertEqual(viewModel.inputText, "Auto transcript")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(viewModel.isTranscribing)
        XCTAssertEqual(transcriptionService.stopCallCount, 0)
    }
}

@MainActor
private final class MockChatSpeechTranscriber: SpeechTranscribing {
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptFinal: ((String) -> Void)?
    var onError: ((String) -> Void)?
    var onStateChange: ((SpeechTranscriptionState) -> Void)?
    var onStopReason: ((SpeechStopReason) -> Void)?

    var isAvailable = true
    var isRecording = false
    var isTranscribing = false
    private(set) var startCallCount = 0
    private(set) var stopCallCount = 0

    private let transcript: String

    init(transcript: String) {
        self.transcript = transcript
    }

    func start() async throws {
        startCallCount += 1
        isRecording = true
        onStateChange?(.recording)
    }

    func stop() async throws -> String {
        stopCallCount += 1
        isRecording = false
        isTranscribing = true
        onStateChange?(.transcribing)
        onTranscriptFinal?(transcript)
        isTranscribing = false
        onStopReason?(.manual)
        onStateChange?(.idle)
        return transcript
    }

    func simulateSilenceAutoStop() async {
        isRecording = false
        isTranscribing = true
        onStateChange?(.transcribing)
        onTranscriptFinal?(transcript)
        isTranscribing = false
        onStopReason?(.silenceAutoStop)
        onStateChange?(.idle)
    }

    func cancel() {
        reset()
        onStopReason?(.cancel)
    }

    func reset() {
        isRecording = false
        isTranscribing = false
        onStateChange?(.idle)
    }
}
