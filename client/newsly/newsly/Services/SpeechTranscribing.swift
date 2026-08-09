import Foundation

let speechAppDidEnterBackgroundNotification = Notification.Name(
    "UIApplicationDidEnterBackgroundNotification"
)

enum SpeechTranscriptionState: Equatable {
    case idle
    case starting
    case recording
    case transcribing
    case failed(String)

    var accessibilityValue: String {
        switch self {
        case .idle: "Idle"
        case .starting: "Starting"
        case .recording: "Recording"
        case .transcribing: "Transcribing"
        case .failed: "Failed"
        }
    }
}

enum SpeechStopReason: Equatable {
    case manual
    case silenceAutoStop
    case noSpeechTimeout
    case maximumDuration
    case cancel
    case failure
}

enum SpeechTranscriptionEvent: Equatable {
    case transcriptDelta(String)
    case transcriptFinal(String)
    case error(String)
    case stateChange(SpeechTranscriptionState)
    case stopReason(SpeechStopReason)
}

struct SpeechRecordingDeadlines: Equatable, Sendable {
    static let standard = SpeechRecordingDeadlines(
        noSpeechTimeoutSeconds: 10,
        maximumDurationSeconds: 60
    )

    let noSpeechTimeoutSeconds: TimeInterval
    let maximumDurationSeconds: TimeInterval

    init(
        noSpeechTimeoutSeconds: TimeInterval,
        maximumDurationSeconds: TimeInterval
    ) {
        precondition(noSpeechTimeoutSeconds > 0)
        precondition(maximumDurationSeconds > 0)
        self.noSpeechTimeoutSeconds = noSpeechTimeoutSeconds
        self.maximumDurationSeconds = maximumDurationSeconds
    }
}

enum VoiceDictationError: LocalizedError {
    case sessionBusy
    case noActiveSession
    case notAuthenticated
    case recordingFailed
    case transcriptionFailed(String)
    case transcriptionTimedOut
    case noMicrophoneAccess
    case audioSessionError(Error)

    var errorDescription: String? {
        switch self {
        case .sessionBusy:
            return "The microphone is already in use. Finish the other recording and try again."
        case .noActiveSession:
            return "There is no active voice recording."
        case .notAuthenticated:
            return "You must be signed in to use voice dictation."
        case .recordingFailed:
            return "Failed to record audio."
        case .transcriptionFailed(let message):
            return "Transcription failed: \(message)"
        case .transcriptionTimedOut:
            return "Transcription timed out. Try a shorter recording or check your connection."
        case .noMicrophoneAccess:
            return "Microphone access denied."
        case .audioSessionError(let error):
            return "Audio session error: \(error.localizedDescription)"
        }
    }
}

/// One exclusively owned recording. Cancellation releases its provider synchronously.
@MainActor
final class SpeechTranscriptionSession {
    let id: UUID
    let events: AsyncStream<SpeechTranscriptionEvent>

    private let startHandler: @MainActor (UUID) async throws -> Void
    private let stopHandler: @MainActor (UUID) async throws -> String
    private let cancelHandler: @MainActor (UUID) -> Void
    private var isReleased = false

    init(
        id: UUID,
        events: AsyncStream<SpeechTranscriptionEvent>,
        start: @escaping @MainActor (UUID) async throws -> Void,
        stop: @escaping @MainActor (UUID) async throws -> String,
        cancel: @escaping @MainActor (UUID) -> Void
    ) {
        self.id = id
        self.events = events
        self.startHandler = start
        self.stopHandler = stop
        self.cancelHandler = cancel
    }

    func start() async throws {
        guard !isReleased else { throw VoiceDictationError.noActiveSession }
        try await startHandler(id)
        guard !isReleased else { throw VoiceDictationError.noActiveSession }
    }

    func stop() async throws -> String {
        guard !isReleased else { throw VoiceDictationError.noActiveSession }
        do {
            let transcript = try await stopHandler(id)
            guard !isReleased else { throw CancellationError() }
            isReleased = true
            return transcript
        } catch {
            if !isReleased {
                isReleased = true
                cancelHandler(id)
            }
            throw error
        }
    }

    func cancel() {
        guard !isReleased else { return }
        isReleased = true
        cancelHandler(id)
    }
}

@MainActor
protocol SpeechTranscribing: AnyObject {
    var isAvailable: Bool { get }
    /// Reserves exclusive ownership before any permission or recorder work awaits.
    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession
}

extension SpeechTranscribing {
    var isAvailable: Bool {
        TokenRefreshService.shared.hasStoredCredentialMaterial
            && AppSettings.shared.backendTranscriptionAvailable
    }
}

@MainActor
enum SpeechTranscriberFactory {
    static func makeVoiceDictationTranscriber() -> any SpeechTranscribing {
#if DEBUG
        if E2ETestLaunch.fakeSpeechEnabled {
            return E2EScriptedSpeechTranscriber.shared
        }
#endif
        return VoiceDictationService.shared
    }
}

#if DEBUG
enum E2ESpeechScenario: String, CaseIterable {
    case success
    case emptyTranscript = "empty"
    case startFailure = "start_failure"
    case transcriptionFailure = "transcription_failure"
    case silenceAutoStop = "silence_auto_stop"
    case noSpeechTimeout = "no_speech_timeout"
    case maximumDuration = "maximum_duration"
}

/// Deterministic debug-only speech driver. It exercises the same exclusive-session
/// contract as the recorder while allowing E2E flows to select failure and timeout paths.
@MainActor
final class E2EScriptedSpeechTranscriber: SpeechTranscribing {
    static let shared = E2EScriptedSpeechTranscriber(
        scenario: E2ESpeechScenario(
            rawValue: E2ETestLaunch.fakeSpeechScenario ?? "success"
        ) ?? .success,
        transcript: E2ETestLaunch.fakeSpeechTranscript
            ?? OnboardingE2EFixtureStore.shared?.transcript
            ?? "E2E transcript"
    )

    var isAvailable: Bool { true }

    private var activeSessionID: UUID?
    private var continuation: AsyncStream<SpeechTranscriptionEvent>.Continuation?
    private var scriptedCompletionTask: Task<Void, Never>?
    private var backgroundObserver: NSObjectProtocol?
    private let scenario: E2ESpeechScenario
    private let transcript: String

    init(scenario: E2ESpeechScenario, transcript: String) {
        self.scenario = scenario
        self.transcript = transcript
        backgroundObserver = NotificationCenter.default.addObserver(
            forName: speechAppDidEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self, let sessionID = self.activeSessionID else { return }
                self.cancel(sessionID: sessionID)
            }
        }
    }

    deinit {
        if let backgroundObserver {
            NotificationCenter.default.removeObserver(backgroundObserver)
        }
    }

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
                guard let self else { throw VoiceDictationError.noActiveSession }
                try await self.start(sessionID: id)
            },
            stop: { [weak self] id in
                guard let self else { throw VoiceDictationError.noActiveSession }
                return try await self.stop(sessionID: id)
            },
            cancel: { [weak self] id in
                self?.cancel(sessionID: id)
            }
        )
    }

    private func start(sessionID: UUID) async throws {
        guard activeSessionID == sessionID else {
            throw VoiceDictationError.noActiveSession
        }
        if scenario == .startFailure {
            release(sessionID: sessionID)
            throw VoiceDictationError.recordingFailed
        }

        try? await Task.sleep(for: .milliseconds(150))
        guard activeSessionID == sessionID else { throw VoiceDictationError.noActiveSession }
        scheduleAutomaticCompletionIfNeeded(sessionID: sessionID)
    }

    private func stop(sessionID: UUID) async throws -> String {
        guard activeSessionID == sessionID, let continuation else {
            throw VoiceDictationError.noActiveSession
        }
        scriptedCompletionTask?.cancel()
        scriptedCompletionTask = nil
        continuation.yield(.stateChange(.transcribing))
        try? await Task.sleep(for: .milliseconds(150))

        if scenario == .transcriptionFailure {
            let message = "The scripted transcription failed."
            continuation.yield(.stateChange(.failed(message)))
            continuation.yield(.error(message))
            continuation.yield(.stopReason(.failure))
            release(sessionID: sessionID)
            throw VoiceDictationError.transcriptionFailed(message)
        }

        let result = scenario == .emptyTranscript ? "" : transcript
        continuation.yield(.transcriptFinal(result))
        continuation.yield(.stateChange(.idle))
        continuation.yield(.stopReason(.manual))
        release(sessionID: sessionID)
        return result
    }

    private func cancel(sessionID: UUID) {
        guard activeSessionID == sessionID, let continuation else { return }
        scriptedCompletionTask?.cancel()
        scriptedCompletionTask = nil
        continuation.yield(.stateChange(.idle))
        continuation.yield(.stopReason(.cancel))
        release(sessionID: sessionID)
    }

    private func scheduleAutomaticCompletionIfNeeded(sessionID: UUID) {
        let stopReason: SpeechStopReason
        switch scenario {
        case .silenceAutoStop:
            stopReason = .silenceAutoStop
        case .noSpeechTimeout:
            stopReason = .noSpeechTimeout
        case .maximumDuration:
            stopReason = .maximumDuration
        case .success, .emptyTranscript, .startFailure, .transcriptionFailure:
            return
        }

        scriptedCompletionTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            self?.completeAutomatically(sessionID: sessionID, reason: stopReason)
        }
    }

    private func completeAutomatically(sessionID: UUID, reason: SpeechStopReason) {
        guard activeSessionID == sessionID, let continuation else { return }
        if reason == .noSpeechTimeout {
            let message = "No speech detected. Try again."
            continuation.yield(.stateChange(.failed(message)))
            continuation.yield(.error(message))
        } else {
            continuation.yield(.stateChange(.transcribing))
            continuation.yield(.transcriptFinal(transcript))
            continuation.yield(.stateChange(.idle))
        }
        continuation.yield(.stopReason(reason))
        release(sessionID: sessionID)
    }

    private func release(sessionID: UUID) {
        guard activeSessionID == sessionID else { return }
        activeSessionID = nil
        continuation?.finish()
        continuation = nil
        scriptedCompletionTask?.cancel()
        scriptedCompletionTask = nil
    }
}
#endif
