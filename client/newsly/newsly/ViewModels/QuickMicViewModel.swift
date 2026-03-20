import Foundation
import os.log

private let quickMicLogger = Logger(subsystem: "com.newsly", category: "QuickMic")

@MainActor
protocol QuickMicChatServicing: AnyObject {
    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse

    func waitForMessageCompletion(messageId: Int) async throws -> ChatMessage
}

extension ChatService: QuickMicChatServicing {}

@MainActor
final class QuickMicViewModel: ObservableObject {
    enum State: Equatable {
        case idle
        case recordingWaveform
        case finalizingTranscript
        case submittingTurn
        case modalActive
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var isAvailable = false
    @Published private(set) var activeSession: ChatSessionSummary?
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var errorMessage: String?
    @Published private(set) var activeTranscript = ""

    private let transcriptionService: any SpeechTranscribing
    private let chatService: any QuickMicChatServicing

    private var activeScreenContext = AssistantScreenContext(screenType: "unknown")
    private var metricsStartedAt: Date?
    private var releaseAt: Date?

    init(
        transcriptionService: (any SpeechTranscribing)? = nil,
        chatService: any QuickMicChatServicing = ChatService.shared
    ) {
        self.transcriptionService = transcriptionService ?? RealtimeTranscriptionService()
        self.chatService = chatService
        configureTranscriptionCallbacks()
    }

    var isRecording: Bool {
        state == .recordingWaveform
    }

    private var hasRetainedPanelContent: Bool {
        activeSession != nil || !messages.isEmpty || errorMessage != nil
    }

    var isModalPresented: Bool {
        switch state {
        case .idle:
            return hasRetainedPanelContent
        case .recordingWaveform:
            return hasRetainedPanelContent
        case .finalizingTranscript, .submittingTurn, .modalActive, .failed:
            return true
        }
    }

    var statusText: String {
        switch state {
        case .idle:
            return "Hold to ask"
        case .recordingWaveform:
            return "Recording..."
        case .finalizingTranscript:
            return "Finalizing..."
        case .submittingTurn:
            return "Thinking..."
        case .modalActive:
            return "Hold to ask again"
        case .failed:
            return "Something went wrong"
        }
    }

    func refreshAvailability() async {
        isAvailable = await OpenAIService.shared.refreshTranscriptionAvailability()
    }

    func beginHold(screenContext: AssistantScreenContext) async {
        guard !isRecording else { return }
        guard transcriptionService.isAvailable else {
            isAvailable = false
            presentError("Voice transcription is unavailable right now.")
            return
        }

        activeScreenContext = screenContext
        activeTranscript = ""
        errorMessage = nil
        metricsStartedAt = Date()
        releaseAt = nil

        do {
            try await transcriptionService.start()
            state = .recordingWaveform
            quickMicLogger.info(
                "Quick mic recording started | screenType=\(screenContext.screenType, privacy: .public)"
            )
        } catch {
            presentError(error.localizedDescription)
        }
    }

    func endHold() async {
        guard isRecording else { return }

        state = .finalizingTranscript
        releaseAt = Date()
        logMetric("press_to_release_ms", from: metricsStartedAt)

        do {
            let transcript = try await transcriptionService.stop().trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            logMetric("release_to_final_transcript_ms", from: releaseAt)

            guard !transcript.isEmpty else {
                presentError("I didn't catch that. Try again.")
                return
            }

            activeTranscript = transcript
            await submitTranscript(transcript)
        } catch {
            presentError(error.localizedDescription)
        }
    }

    func cancelHold() {
        transcriptionService.cancel()
        state = messages.isEmpty ? .idle : .modalActive
        activeTranscript = ""
    }

    func dismissPanel() {
        guard !isRecording else { return }
        transcriptionService.reset()
        state = .idle
        activeSession = nil
        messages = []
        errorMessage = nil
        activeTranscript = ""
    }

    private func submitTranscript(_ transcript: String) async {
        state = .submittingTurn
        errorMessage = nil

        do {
            let response = try await chatService.createAssistantTurn(
                message: transcript,
                sessionId: activeSession?.id,
                screenContext: activeScreenContext
            )
            logMetric("release_to_assistant_request_ms", from: releaseAt)

            if activeSession?.id != response.session.id {
                messages = []
            }
            activeSession = response.session
            activeTranscript = ""
            messages = [response.userMessage]
            logMetric("release_to_modal_pending_ms", from: releaseAt)

            let assistantMessage = try await chatService.waitForMessageCompletion(
                messageId: response.messageId
            )
            messages = [response.userMessage, assistantMessage]
            state = .modalActive
            logMetric("release_to_assistant_complete_ms", from: releaseAt)
        } catch {
            presentError(error.localizedDescription)
        }
    }

    private func configureTranscriptionCallbacks() {
        transcriptionService.onTranscriptDelta = { [weak self] delta in
            guard let self else { return }
            guard self.isRecording else { return }
            self.activeTranscript.append(delta)
        }
        transcriptionService.onTranscriptFinal = { [weak self] transcript in
            guard let self else { return }
            guard self.isRecording || self.state == .finalizingTranscript else { return }
            self.activeTranscript = transcript
        }
        transcriptionService.onError = { [weak self] message in
            self?.handleTranscriptionError(message)
        }
    }

    private func handleTranscriptionError(_ message: String) {
        switch state {
        case .recordingWaveform, .finalizingTranscript:
            presentError(message)
        case .idle, .submittingTurn, .modalActive, .failed:
            quickMicLogger.debug(
                "Ignoring stale transcription error outside capture flow: \(message, privacy: .public)"
            )
        }
    }

    private func presentError(_ message: String) {
        errorMessage = message
        state = .failed(message)
        quickMicLogger.error("Quick mic failed: \(message, privacy: .public)")
    }

    private func logMetric(_ name: String, from start: Date?) {
        guard let start else { return }
        let elapsedMs = Int(Date().timeIntervalSince(start) * 1000)
        quickMicLogger.info("\(name, privacy: .public)=\(elapsedMs, privacy: .public)")
    }
}
