import Foundation

/// Owns exactly one speech session and its event consumer. Features interact
/// through this type rather than sharing recorder callbacks directly.
@MainActor
final class VoiceDictationCoordinator {
    private let transcriber: any SpeechTranscribing
    private let deadlines: SpeechRecordingDeadlines

    private var session: SpeechTranscriptionSession?
    private var eventTask: Task<Void, Never>?
    private var transcriptDeliveryTask: Task<Void, Never>?
    private var onTranscriptFinal: (@MainActor (String) async -> Void)?
    private var onError: (@MainActor (String) async -> Void)?
    private var onStateChange: (@MainActor (SpeechTranscriptionState) async -> Void)?
    private var onStopReason: (@MainActor (SpeechStopReason) async -> Void)?
    private var pendingFailureMessage: String?

    init(
        transcriber: any SpeechTranscribing,
        deadlines: SpeechRecordingDeadlines = .standard
    ) {
        self.transcriber = transcriber
        self.deadlines = deadlines
    }

    var isAvailable: Bool { transcriber.isAvailable }
    var hasActiveSession: Bool { session != nil }

    func start(
        onTranscriptFinal: (@MainActor (String) async -> Void)? = nil,
        onError: (@MainActor (String) async -> Void)? = nil,
        onStateChange: (@MainActor (SpeechTranscriptionState) async -> Void)? = nil,
        onStopReason: (@MainActor (SpeechStopReason) async -> Void)? = nil
    ) async throws {
        guard session == nil else { throw VoiceDictationError.sessionBusy }

        let startingSession: SpeechTranscriptionSession
        do {
            startingSession = try transcriber.makeSession(deadlines: deadlines)
        } catch {
            let message = error.localizedDescription
            await onStateChange?(.failed(message))
            await onError?(message)
            throw error
        }

        session = startingSession
        pendingFailureMessage = nil
        self.onTranscriptFinal = onTranscriptFinal
        self.onError = onError
        self.onStateChange = onStateChange
        self.onStopReason = onStopReason
        consumeEvents(from: startingSession)
        await onStateChange?(.starting)

        do {
            try await startingSession.start()
            guard session?.id == startingSession.id else {
                throw CancellationError()
            }
            await onStateChange?(.recording)
        } catch {
            guard session?.id == startingSession.id else {
                throw CancellationError()
            }
            let message = error.localizedDescription
            let stateChangeCallback = self.onStateChange
            let errorCallback = self.onError
            releaseSession(
                expectedSessionID: startingSession.id,
                cancelRecorder: true
            )
            await stateChangeCallback?(.failed(message))
            await errorCallback?(message)
            throw error
        }
    }

    func stop() async throws -> String {
        guard let session else { throw VoiceDictationError.noActiveSession }
        // Manual callers own the returned transcript. Stop consuming before the
        // service publishes its buffered completion to avoid duplicate submission.
        eventTask?.cancel()
        eventTask = nil
        do {
            let transcript = try await session.stop()
            guard self.session?.id == session.id else {
                throw CancellationError()
            }
            releaseSession(
                expectedSessionID: session.id,
                cancelRecorder: false
            )
            return transcript
        } catch {
            releaseSession(
                expectedSessionID: session.id,
                cancelRecorder: true
            )
            throw error
        }
    }

    /// Releases recorder ownership before returning so another surface can start immediately.
    func cancel() {
        guard let sessionID = session?.id else { return }
        releaseSession(expectedSessionID: sessionID, cancelRecorder: true)
    }

    private func consumeEvents(from session: SpeechTranscriptionSession) {
        eventTask?.cancel()
        let sessionID = session.id
        eventTask = Task { @MainActor [weak self] in
            for await event in session.events {
                guard let self, !Task.isCancelled else { return }
                await self.handle(event, sessionID: sessionID)
            }
            guard let self, !Task.isCancelled else { return }
            await self.finishPendingFailureIfNeeded(sessionID: sessionID)
        }
    }

    private func handle(_ event: SpeechTranscriptionEvent, sessionID: UUID) async {
        guard session?.id == sessionID else { return }

        switch event {
        case .transcriptDelta:
            return
        case .transcriptFinal(let transcript):
            let callback = onTranscriptFinal
            transcriptDeliveryTask?.cancel()
            transcriptDeliveryTask = Task { @MainActor in
                guard !Task.isCancelled else { return }
                await callback?(transcript)
            }
        case .error(let message):
            pendingFailureMessage = message
        case .stateChange(.failed(let message)):
            pendingFailureMessage = message
        case .stateChange(let state):
            await onStateChange?(state)
        case .stopReason(let reason):
            let transcriptDeliveryTask = transcriptDeliveryTask
            let failureMessage = pendingFailureMessage
            let stateChangeCallback = onStateChange
            let errorCallback = onError
            let stopReasonCallback = onStopReason
            if reason != .manual, session?.id == sessionID {
                // The provider has already reached a terminal state. Drop
                // coordinator ownership before awaiting downstream work such as
                // onboarding discovery or a chat send.
                releaseSession(
                    expectedSessionID: sessionID,
                    cancelRecorder: false,
                    cancelEventConsumer: false,
                    cancelTranscriptDelivery: false
                )
            }
            await transcriptDeliveryTask?.value
            if let failureMessage {
                await stateChangeCallback?(.failed(failureMessage))
                await errorCallback?(failureMessage)
            }
            await stopReasonCallback?(reason)
        }
    }

    private func finishPendingFailureIfNeeded(sessionID: UUID) async {
        guard session?.id == sessionID,
              let failureMessage = pendingFailureMessage else {
            return
        }
        let stateChangeCallback = onStateChange
        let errorCallback = onError
        let stopReasonCallback = onStopReason
        releaseSession(
            expectedSessionID: sessionID,
            cancelRecorder: false,
            cancelEventConsumer: false
        )
        await stateChangeCallback?(.failed(failureMessage))
        await errorCallback?(failureMessage)
        await stopReasonCallback?(.failure)
    }

    private func releaseSession(
        expectedSessionID: UUID,
        cancelRecorder: Bool,
        cancelEventConsumer: Bool = true,
        cancelTranscriptDelivery: Bool = true
    ) {
        guard let activeSession = session,
              activeSession.id == expectedSessionID else {
            return
        }
        session = nil
        if cancelRecorder {
            activeSession.cancel()
        }
        if cancelEventConsumer {
            eventTask?.cancel()
        }
        eventTask = nil
        if cancelTranscriptDelivery {
            transcriptDeliveryTask?.cancel()
        }
        transcriptDeliveryTask = nil
        onTranscriptFinal = nil
        onError = nil
        onStateChange = nil
        onStopReason = nil
        pendingFailureMessage = nil
    }
}
