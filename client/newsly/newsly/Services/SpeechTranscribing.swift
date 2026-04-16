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
    var isAvailable: Bool {
        let accessToken = KeychainManager.shared.getToken(key: .accessToken)
        let refreshToken = KeychainManager.shared.getToken(key: .refreshToken)
        let hasAuthToken = !(accessToken?.isEmpty ?? true) || !(refreshToken?.isEmpty ?? true)
        return hasAuthToken && AppSettings.shared.backendTranscriptionAvailable
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
