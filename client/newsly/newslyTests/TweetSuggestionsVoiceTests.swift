import Foundation
import XCTest
@testable import newsly

@MainActor
final class TweetSuggestionsVoiceTests: XCTestCase {
    func testTweetVoicePassesStandardDeadlinesToProvider() async {
        let speech = TweetSpeechTranscriber(transcript: "unused")
        let viewModel = makeViewModel(speech: speech)

        await viewModel.startVoiceRecording()

        XCTAssertEqual(speech.requestedDeadlines, [.standard])
        viewModel.cancelVoiceRecording()
    }

    func testTweetVoiceSessionCancelsAndReleasesOwnership() async {
        let speech = TweetSpeechTranscriber(transcript: "make it sharper")
        let viewModel = makeViewModel(speech: speech)

        await viewModel.startVoiceRecording()

        XCTAssertTrue(viewModel.isRecording)
        XCTAssertEqual(viewModel.voiceState, .recording)
        XCTAssertTrue(speech.hasActiveSession)

        viewModel.cancelVoiceRecording()

        XCTAssertFalse(viewModel.isRecording)
        XCTAssertEqual(viewModel.voiceState, .idle)
        XCTAssertFalse(speech.hasActiveSession)
        XCTAssertEqual(speech.cancelCallCount, 1)
    }

    func testTweetVoiceTranscriptRegeneratesWithSpokenAdjustment() async {
        let speech = TweetSpeechTranscriber(transcript: "make it sharper")
        let content = TweetContentServiceStub()
        let viewModel = makeViewModel(speech: speech, content: content)
        await viewModel.initialize(contentId: 7)

        await viewModel.startVoiceRecording()
        await viewModel.stopVoiceRecording()

        XCTAssertEqual(viewModel.tweakMessage, "make it sharper")
        XCTAssertEqual(content.messages, [nil, "make it sharper"])
        XCTAssertEqual(viewModel.voiceState, .idle)
        XCTAssertFalse(speech.hasActiveSession)
    }

    func testLatestVoiceAdjustedRequestWinsWhenInitialRequestFinishesLast() async {
        let speech = TweetSpeechTranscriber(transcript: "unused")
        let content = DeferredTweetContentService()
        let viewModel = makeViewModel(speech: speech, content: content)

        let initialTask = Task { await viewModel.initialize(contentId: 7) }
        let didStartInitialRequest = await waitUntil { content.requestCount == 1 }
        XCTAssertTrue(didStartInitialRequest)

        viewModel.tweakMessage = "make it sharper"
        let voiceAdjustedTask = Task { await viewModel.regenerate() }
        let didStartVoiceAdjustedRequest = await waitUntil { content.requestCount == 2 }
        XCTAssertTrue(didStartVoiceAdjustedRequest)

        content.resolveRequest(
            at: 1,
            suggestions: [TweetSuggestion(id: 2, text: "Latest voice result", styleLabel: nil)]
        )
        await voiceAdjustedTask.value
        content.resolveRequest(
            at: 0,
            suggestions: [TweetSuggestion(id: 1, text: "Stale initial result", styleLabel: nil)]
        )
        await initialTask.value

        XCTAssertEqual(content.messages, [nil, "make it sharper"])
        XCTAssertEqual(viewModel.suggestions.map(\.text), ["Latest voice result"])
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertFalse(viewModel.isLoading)
        XCTAssertFalse(viewModel.isRegenerating)
    }

    func testTweetVoiceSilenceAutoStopRegenerates() async {
        await assertAutomaticCompletion(reason: .silenceAutoStop)
    }

    func testTweetVoiceMaximumDurationRegenerates() async {
        await assertAutomaticCompletion(reason: .maximumDuration)
    }

    func testTweetVoiceNoSpeechFailureIsRetryable() async {
        let speech = TweetSpeechTranscriber(transcript: "unused")
        let content = TweetContentServiceStub()
        let viewModel = makeViewModel(speech: speech, content: content)
        await viewModel.initialize(contentId: 7)

        await viewModel.startVoiceRecording()
        speech.completeWithoutSpeech()
        let didFail = await waitUntil {
            viewModel.errorMessage == "No speech detected. Try again."
        }

        XCTAssertTrue(didFail)
        XCTAssertEqual(viewModel.voiceState, .failed("No speech detected. Try again."))
        XCTAssertEqual(content.messages, [nil])
        XCTAssertFalse(speech.hasActiveSession)

        var didRestart = false
        for _ in 0..<100 {
            await Task.yield()
            if !viewModel.isRecording {
                await viewModel.startVoiceRecording()
            }
            if viewModel.isRecording {
                didRestart = true
                break
            }
        }
        XCTAssertTrue(didRestart)
        XCTAssertNil(viewModel.errorMessage)
        viewModel.cancelVoiceRecording()
    }

    func testTweetVoiceEmptyTranscriptOffersVoiceRetryInsteadOfRegeneration() async {
        let speech = TweetSpeechTranscriber(transcript: "   ")
        let content = TweetContentServiceStub()
        let viewModel = makeViewModel(speech: speech, content: content)
        await viewModel.initialize(contentId: 7)

        await viewModel.startVoiceRecording()
        await viewModel.stopVoiceRecording()

        XCTAssertEqual(viewModel.errorMessage, "I didn't catch that. Try again.")
        XCTAssertTrue(viewModel.hasVoiceError)
        XCTAssertEqual(content.messages, [nil])

        await viewModel.retryVoiceRecording()
        XCTAssertTrue(viewModel.isRecording)
        XCTAssertNil(viewModel.errorMessage)
        viewModel.cancelVoiceRecording()
    }

    func testTweetVoiceStartFailureSurfacesErrorAndReleasesSession() async {
        let speech = TweetSpeechTranscriber(
            transcript: "unused",
            startError: VoiceDictationError.recordingFailed
        )
        let viewModel = makeViewModel(speech: speech)

        await viewModel.startVoiceRecording()

        XCTAssertEqual(viewModel.voiceState, .failed("Failed to record audio."))
        XCTAssertEqual(viewModel.errorMessage, "Failed to record audio.")
        XCTAssertFalse(viewModel.isRecording)
        XCTAssertFalse(speech.hasActiveSession)
    }

    func testTweetVoiceTranscriptionFailureSurfacesErrorAndReleasesSession() async {
        let speech = TweetSpeechTranscriber(
            transcript: "unused",
            stopError: VoiceDictationError.transcriptionFailed("scripted failure")
        )
        let content = TweetContentServiceStub()
        let viewModel = makeViewModel(speech: speech, content: content)
        await viewModel.initialize(contentId: 7)

        await viewModel.startVoiceRecording()
        await viewModel.stopVoiceRecording()

        XCTAssertEqual(
            viewModel.voiceState,
            .failed("Transcription failed: scripted failure")
        )
        XCTAssertEqual(viewModel.errorMessage, "Transcription failed: scripted failure")
        XCTAssertEqual(content.messages, [nil])
        XCTAssertFalse(speech.hasActiveSession)
    }

    private func makeViewModel(
        speech: TweetSpeechTranscriber,
        content: any TweetSuggestionContentServicing = TweetContentServiceStub()
    ) -> TweetSuggestionsViewModel {
        TweetSuggestionsViewModel(
            contentService: content,
            twitterService: TweetSharingStub(),
            transcriptionService: speech,
            refreshTranscriptionAvailability: { true },
            setBackendTranscriptionAvailable: { _ in }
        )
    }

    private func assertAutomaticCompletion(reason: SpeechStopReason) async {
        let speech = TweetSpeechTranscriber(transcript: "automatic adjustment")
        let content = TweetContentServiceStub()
        let viewModel = makeViewModel(speech: speech, content: content)
        await viewModel.initialize(contentId: 7)

        await viewModel.startVoiceRecording()
        speech.completeAutomatically(reason: reason)
        let didRegenerate = await waitUntil {
            content.messages == [nil, "automatic adjustment"]
        }

        XCTAssertTrue(didRegenerate)
        XCTAssertEqual(viewModel.tweakMessage, "automatic adjustment")
        XCTAssertEqual(viewModel.voiceState, .idle)
        XCTAssertFalse(speech.hasActiveSession)
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

private final class DeferredTweetContentService: TweetSuggestionContentServicing {
    private(set) var messages: [String?] = []
    private var continuations: [
        Int: CheckedContinuation<TweetSuggestionsResponse, Error>
    ] = [:]

    var requestCount: Int { messages.count }

    func generateTweetSuggestions(
        id: Int,
        message: String?,
        creativity: Int,
        provider: ChatModelProvider?
    ) async throws -> TweetSuggestionsResponse {
        _ = provider
        let requestIndex = messages.count
        messages.append(message)
        return try await withCheckedThrowingContinuation { continuation in
            continuations[requestIndex] = continuation
        }
    }

    func resolveRequest(at index: Int, suggestions: [TweetSuggestion]) {
        let continuation = continuations.removeValue(forKey: index)
        continuation?.resume(
            returning: TweetSuggestionsResponse(
                contentId: 7,
                creativity: 5,
                model: "test",
                suggestions: suggestions
            )
        )
    }
}

@MainActor
private final class TweetSpeechTranscriber: SpeechTranscribing {
    var isAvailable = true
    private(set) var hasActiveSession = false
    private(set) var cancelCallCount = 0
    private(set) var requestedDeadlines: [SpeechRecordingDeadlines] = []

    private let transcript: String
    private let startError: Error?
    private let stopError: Error?
    private var activeSessionID: UUID?
    private var continuation: AsyncStream<SpeechTranscriptionEvent>.Continuation?

    init(
        transcript: String,
        startError: Error? = nil,
        stopError: Error? = nil
    ) {
        self.transcript = transcript
        self.startError = startError
        self.stopError = stopError
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
                if let startError = self.startError {
                    self.release()
                    pair.continuation.finish()
                    throw startError
                }
            },
            stop: { [weak self] id in
                guard let self, self.activeSessionID == id else {
                    throw VoiceDictationError.noActiveSession
                }
                if let stopError = self.stopError {
                    self.release()
                    pair.continuation.finish()
                    throw stopError
                }
                let transcript = self.transcript
                self.release()
                pair.continuation.finish()
                return transcript
            },
            cancel: { [weak self] id in
                guard let self, self.activeSessionID == id else { return }
                self.cancelCallCount += 1
                self.release()
                pair.continuation.finish()
            }
        )
    }

    func completeAutomatically(reason: SpeechStopReason) {
        continuation?.yield(.stateChange(.transcribing))
        continuation?.yield(.transcriptFinal(transcript))
        continuation?.yield(.stateChange(.idle))
        continuation?.yield(.stopReason(reason))
        releaseAndFinish()
    }

    func completeWithoutSpeech() {
        let message = "No speech detected. Try again."
        continuation?.yield(.stateChange(.failed(message)))
        continuation?.yield(.error(message))
        continuation?.yield(.stopReason(.noSpeechTimeout))
        releaseAndFinish()
    }

    private func releaseAndFinish() {
        let continuation = continuation
        release()
        continuation?.finish()
    }

    private func release() {
        activeSessionID = nil
        continuation = nil
        hasActiveSession = false
    }
}

private final class TweetContentServiceStub: TweetSuggestionContentServicing {
    private(set) var messages: [String?] = []

    func generateTweetSuggestions(
        id: Int,
        message: String?,
        creativity: Int,
        provider: ChatModelProvider?
    ) async throws -> TweetSuggestionsResponse {
        messages.append(message)
        return TweetSuggestionsResponse(
            contentId: id,
            creativity: creativity,
            model: "test",
            suggestions: []
        )
    }
}

@MainActor
private final class TweetSharingStub: TweetSharing {
    func share(tweet: String, completion: ((Bool) -> Void)?) {
        completion?(true)
    }
}
