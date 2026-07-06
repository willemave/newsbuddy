import Foundation

enum SpeechTranscriptionState: Equatable {
    case idle
    case recording
    case transcribing
}

enum SpeechStopReason: Equatable {
    case manual
    case silenceAutoStop
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

@MainActor
protocol SpeechTranscribing: AnyObject {
    var onTranscriptDelta: ((String) -> Void)? { get set }
    var onTranscriptFinal: ((String) -> Void)? { get set }
    var onError: ((String) -> Void)? { get set }
    var onStateChange: ((SpeechTranscriptionState) -> Void)? { get set }
    var onStopReason: ((SpeechStopReason) -> Void)? { get set }

    var isAvailable: Bool { get }
    var isRecording: Bool { get }
    var isTranscribing: Bool { get }

    func start() async throws
    func stop() async throws -> String
    func cancel()
    func reset()
}

extension SpeechTranscribing {
    func events() -> AsyncStream<SpeechTranscriptionEvent> {
        AsyncStream { continuation in
            onTranscriptDelta = { transcript in
                continuation.yield(.transcriptDelta(transcript))
            }
            onTranscriptFinal = { transcript in
                continuation.yield(.transcriptFinal(transcript))
            }
            onError = { message in
                continuation.yield(.error(message))
            }
            onStateChange = { state in
                continuation.yield(.stateChange(state))
            }
            onStopReason = { reason in
                continuation.yield(.stopReason(reason))
            }
            continuation.onTermination = { @Sendable _ in
                Task { @MainActor in
                    self.onTranscriptDelta = nil
                    self.onTranscriptFinal = nil
                    self.onError = nil
                    self.onStateChange = nil
                    self.onStopReason = nil
                }
            }
        }
    }

    var isAvailable: Bool {
        TokenRefreshService.shared.hasStoredCredentialMaterial
            && AppSettings.shared.backendTranscriptionAvailable
    }
}

@MainActor
enum SpeechTranscriberFactory {
    static func makeVoiceDictationTranscriber() -> any SpeechTranscribing {
        if E2ETestLaunch.fakeSpeechEnabled {
            return E2EFakeSpeechTranscriber()
        }
        return VoiceDictationService.shared
    }
}

@MainActor
private final class E2EFakeSpeechTranscriber: SpeechTranscribing {
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptFinal: ((String) -> Void)?
    var onError: ((String) -> Void)?
    var onStateChange: ((SpeechTranscriptionState) -> Void)?
    var onStopReason: ((SpeechStopReason) -> Void)?

    var isAvailable: Bool { true }
    private(set) var isRecording = false {
        didSet { notifyStateChange() }
    }
    private(set) var isTranscribing = false {
        didSet { notifyStateChange() }
    }

    private let transcript: String

    init(transcript: String? = E2ETestLaunch.fakeSpeechTranscript) {
        self.transcript = transcript
            ?? OnboardingE2EFixtureStore.shared?.transcript
            ?? "E2E transcript"
    }

    func start() async throws {
        guard !isRecording else { return }
        isRecording = true
        isTranscribing = false
    }

    func stop() async throws -> String {
        guard isRecording else { return transcript }

        isRecording = false
        isTranscribing = true
        try? await Task.sleep(nanoseconds: 150_000_000)
        onTranscriptFinal?(transcript)
        isTranscribing = false
        onStopReason?(.manual)
        return transcript
    }

    func cancel() {
        isRecording = false
        isTranscribing = false
        onStopReason?(.cancel)
    }

    func reset() {
        onTranscriptDelta = nil
        onTranscriptFinal = nil
        onError = nil
        onStateChange = nil
        onStopReason = nil
        isRecording = false
        isTranscribing = false
    }

    private func notifyStateChange() {
        if isRecording {
            onStateChange?(.recording)
        } else if isTranscribing {
            onStateChange?(.transcribing)
        } else {
            onStateChange?(.idle)
        }
    }
}
