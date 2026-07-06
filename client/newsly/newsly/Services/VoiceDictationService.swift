
//
//  VoiceDictationService.swift
//  newsly
//
//  Voice dictation service using authenticated backend transcription APIs.
//

import AVFoundation
import Foundation
import Observation
import os

private let logger = Logger(subsystem: "com.newsly", category: "VoiceDictation")
private let voicePerfSignposter = OSSignposter(subsystem: "com.newsly.chat", category: "perf")

private func voiceElapsedMilliseconds(since start: Date) -> Int {
    Int(Date().timeIntervalSince(start) * 1000)
}

private enum SilenceDetectionConfig {
    static let meteringIntervalSeconds: TimeInterval = 0.1
    static let calibrationWindowSeconds: TimeInterval = 0.3
    static let speechMarginDb: Float = 12
    static let minimumSpeechThresholdDb: Float = -42
    static let silenceHysteresisDb: Float = 6
    static let silenceTimeoutSeconds: TimeInterval = 4
    static let minimumRecordingDurationForAutoStopSeconds: TimeInterval = 0.75
}

private final class AudioRecordingSessionLease {
    private let audioSession = AVAudioSession.sharedInstance()
    private var isActive = false

    func activate() throws {
        try audioSession.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
        try audioSession.setActive(true)
        isActive = true
        voicePerfSignposter.emitEvent("audio-session-activate")
    }

    func deactivate() {
        guard isActive else { return }

        do {
            try audioSession.setActive(false, options: .notifyOthersOnDeactivation)
            voicePerfSignposter.emitEvent("audio-session-deactivate")
        } catch {
            logger.error("Failed to deactivate audio session: \(error.localizedDescription)")
        }
        isActive = false
    }
}

/// Error types for voice dictation.
enum VoiceDictationError: LocalizedError {
    case notAuthenticated
    case recordingFailed
    case transcriptionFailed(String)
    case transcriptionTimedOut
    case noMicrophoneAccess
    case audioSessionError(Error)

    var errorDescription: String? {
        switch self {
        case .notAuthenticated:
            return "You must be signed in to use voice dictation."
        case .recordingFailed:
            return "Failed to record audio"
        case .transcriptionFailed(let message):
            return "Transcription failed: \(message)"
        case .transcriptionTimedOut:
            return "Transcription timed out. Try a shorter recording or check your connection."
        case .noMicrophoneAccess:
            return "Microphone access denied"
        case .audioSessionError(let error):
            return "Audio session error: \(error.localizedDescription)"
        }
    }
}

/// Service for voice dictation using the authenticated backend transcription API.
@MainActor
@Observable
final class VoiceDictationService: NSObject, SpeechTranscribing {
    static let shared = VoiceDictationService()

    private(set) var isRecording = false {
        didSet { notifyStateChange() }
    }
    private(set) var isTranscribing = false {
        didSet { notifyStateChange() }
    }

    @ObservationIgnored
    var onTranscriptDelta: ((String) -> Void)?

    @ObservationIgnored
    var onTranscriptFinal: ((String) -> Void)?

    @ObservationIgnored
    var onError: ((String) -> Void)?

    @ObservationIgnored
    var onStateChange: ((SpeechTranscriptionState) -> Void)?

    @ObservationIgnored
    var onStopReason: ((SpeechStopReason) -> Void)?

    @ObservationIgnored
    private var audioRecorder: AVAudioRecorder?

    @ObservationIgnored
    private var recordingURL: URL?

    @ObservationIgnored
    private var meteringTimer: Timer?

    @ObservationIgnored
    private var autoStopTask: Task<Void, Never>?

    @ObservationIgnored
    private var recordingStartedAt: Date?

    @ObservationIgnored
    private var silenceStartedAt: Date?

    @ObservationIgnored
    private var hasDetectedSpeech = false

    @ObservationIgnored
    private var ambientPeakDb: Float = -80

    @ObservationIgnored
    private var speechThresholdDb = SilenceDetectionConfig.minimumSpeechThresholdDb

    @ObservationIgnored
    private var silenceThresholdDb =
        SilenceDetectionConfig.minimumSpeechThresholdDb - SilenceDetectionConfig.silenceHysteresisDb

    @ObservationIgnored
    private var isFinalizing = false

    @ObservationIgnored
    private var interruptionObserver: NSObjectProtocol?

    @ObservationIgnored
    private var routeChangeObserver: NSObjectProtocol?

    @ObservationIgnored
    private let audioSessionLease = AudioRecordingSessionLease()

    @ObservationIgnored
    private let openAIService = OpenAIService.shared

    private override init() {
        super.init()
    }

    /// Request microphone permission.
    func requestMicrophonePermission() async -> Bool {
        let startedAt = Date()
        return await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                logger.info(
                    "Microphone permission resolved | granted=\(granted) elapsedMs=\(voiceElapsedMilliseconds(since: startedAt))"
                )
                continuation.resume(returning: granted)
            }
        }
    }

    func start() async throws {
        do {
            try await startRecording()
        } catch {
            onStopReason?(.failure)
            onError?(error.localizedDescription)
            throw error
        }
    }

    func stop() async throws -> String {
        do {
            return try await stopRecordingAndTranscribe()
        } catch {
            onError?(error.localizedDescription)
            throw error
        }
    }

    func cancel() {
        cancelRecording()
    }

    func reset() {
        clearCallbacks()
        cancelRecording(notifyStopReason: false)
    }

    private func clearCallbacks() {
        onTranscriptDelta = nil
        onTranscriptFinal = nil
        onError = nil
        onStateChange = nil
        onStopReason = nil
    }

    /// Start recording audio.
    func startRecording() async throws {
        let startedAt = Date()
        if !isAvailable {
            _ = await openAIService.refreshTranscriptionAvailability()
        }
        guard isAvailable else {
            logger.error(
                "Voice recording unavailable | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt))"
            )
            throw VoiceDictationError.notAuthenticated
        }

        let hasPermission = await requestMicrophonePermission()
        guard hasPermission else {
            logger.error(
                "Voice recording missing microphone permission | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt))"
            )
            throw VoiceDictationError.noMicrophoneAccess
        }

        do {
            let audioSessionStartedAt = Date()
            try audioSessionLease.activate()
            logger.info(
                "Voice recording audio session active | elapsedMs=\(voiceElapsedMilliseconds(since: audioSessionStartedAt)) totalElapsedMs=\(voiceElapsedMilliseconds(since: startedAt))"
            )
            observeAudioNotifications()
        } catch {
            audioSessionLease.deactivate()
            logger.error(
                "Voice recording audio session failed | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw VoiceDictationError.audioSessionError(error)
        }

        // Create recording URL
        let documentsPath = FileManager.default.temporaryDirectory
        let audioFilename = documentsPath.appendingPathComponent("voice_dictation.m4a")
        recordingURL = audioFilename

        // Recording settings
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16000.0,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue
        ]

        do {
            let recorder = try AVAudioRecorder(url: audioFilename, settings: settings)
            recorder.delegate = self
            recorder.isMeteringEnabled = true
            recorder.prepareToRecord()

            resetSilenceDetectionState()
            recordingStartedAt = Date()

            guard recorder.record() else {
                throw VoiceDictationError.recordingFailed
            }

            audioRecorder = recorder
            startMetering()
            isRecording = true
            logger.info(
                "Started recording | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) sampleRate=16000 channels=1"
            )
        } catch {
            audioSessionLease.deactivate()
            removeAudioNotificationObservers()
            logger.error(
                "Voice recording start failed | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw VoiceDictationError.recordingFailed
        }
    }

    /// Stop recording and transcribe.
    func stopRecordingAndTranscribe() async throws -> String {
        return try await finalizeRecordingAndTranscribe(stopReason: .manual)
    }

    /// Cancel recording without transcribing.
    func cancelRecording() {
        cancelRecording(notifyStopReason: true)
    }

    private func cancelRecording(notifyStopReason: Bool) {
        let startedAt = Date()
        let wasActive = isRecording || isTranscribing || recordingURL != nil
        stopMetering()
        autoStopTask?.cancel()
        autoStopTask = nil
        audioRecorder?.stop()
        audioRecorder = nil
        audioSessionLease.deactivate()
        removeAudioNotificationObservers()
        isRecording = false
        isTranscribing = false
        isFinalizing = false

        // Clean up recording file
        if let url = recordingURL {
            try? FileManager.default.removeItem(at: url)
        }
        recordingURL = nil
        resetSilenceDetectionState()
        recordingStartedAt = nil

        if wasActive, notifyStopReason {
            onStopReason?(.cancel)
        }
        if wasActive {
            logger.info(
                "Voice recording cancelled | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) notifyStopReason=\(notifyStopReason)"
            )
        }
    }

    // MARK: - Private

    private func notifyStateChange() {
        if isRecording {
            onStateChange?(.recording)
        } else if isTranscribing {
            onStateChange?(.transcribing)
        } else {
            onStateChange?(.idle)
        }
    }

    private func finalizeRecordingAndTranscribe(stopReason: SpeechStopReason) async throws -> String {
        let finalizeStartedAt = Date()
        guard isRecording, let recorder = audioRecorder else {
            throw VoiceDictationError.recordingFailed
        }
        guard !isFinalizing else {
            throw VoiceDictationError.recordingFailed
        }

        isFinalizing = true
        let recordingDurationMs = recordingStartedAt.map { voiceElapsedMilliseconds(since: $0) } ?? 0
        stopMetering()
        recorder.stop()
        audioRecorder = nil
        audioSessionLease.deactivate()
        removeAudioNotificationObservers()
        isRecording = false
        logger.info(
            "Stopped recording | stopReason=\(String(describing: stopReason), privacy: .public) finalizeElapsedMs=\(voiceElapsedMilliseconds(since: finalizeStartedAt)) recordingDurationMs=\(recordingDurationMs)"
        )

        guard let url = recordingURL else {
            isFinalizing = false
            throw VoiceDictationError.recordingFailed
        }
        let audioSizeBytes = fileSizeBytes(at: url)
        logger.info(
            "Voice recording ready for transcription | stopReason=\(String(describing: stopReason), privacy: .public) bytes=\(audioSizeBytes) recordingDurationMs=\(recordingDurationMs)"
        )

        isTranscribing = true
        defer {
            isTranscribing = false
            isFinalizing = false
            recordingStartedAt = nil
            resetSilenceDetectionState()
            if let recordingURL {
                try? FileManager.default.removeItem(at: recordingURL)
            }
            recordingURL = nil
        }

        do {
            let transcript = try await transcribeAudio(fileURL: url)
            onTranscriptFinal?(transcript)
            onStopReason?(stopReason)
            logger.info(
                "Voice recording finalized | stopReason=\(String(describing: stopReason), privacy: .public) totalElapsedMs=\(voiceElapsedMilliseconds(since: finalizeStartedAt)) transcriptChars=\(transcript.count)"
            )
            return transcript
        } catch {
            onStopReason?(.failure)
            logger.error(
                "Voice recording finalize failed | stopReason=\(String(describing: stopReason), privacy: .public) totalElapsedMs=\(voiceElapsedMilliseconds(since: finalizeStartedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw error
        }
    }

    private func startMetering() {
        meteringTimer?.invalidate()
        let timer = Timer.scheduledTimer(
            withTimeInterval: SilenceDetectionConfig.meteringIntervalSeconds,
            repeats: true
        ) { [weak self] _ in
            Task { @MainActor in
                self?.handleMeteringTick()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        meteringTimer = timer
    }

    private func stopMetering() {
        meteringTimer?.invalidate()
        meteringTimer = nil
    }

    private func handleMeteringTick() {
        guard isRecording, !isFinalizing, let recorder = audioRecorder else { return }
        recorder.updateMeters()

        let powerDb = recorder.averagePower(forChannel: 0)
        let now = Date()
        if let recordingStartedAt,
           now.timeIntervalSince(recordingStartedAt) <= SilenceDetectionConfig.calibrationWindowSeconds {
            ambientPeakDb = max(ambientPeakDb, powerDb)
            speechThresholdDb = max(
                ambientPeakDb + SilenceDetectionConfig.speechMarginDb,
                SilenceDetectionConfig.minimumSpeechThresholdDb
            )
            silenceThresholdDb = speechThresholdDb - SilenceDetectionConfig.silenceHysteresisDb
        }

        if powerDb >= speechThresholdDb {
            hasDetectedSpeech = true
            silenceStartedAt = nil
            return
        }
        if hasDetectedSpeech, powerDb >= silenceThresholdDb {
            silenceStartedAt = nil
            return
        }

        guard hasDetectedSpeech else { return }
        if silenceStartedAt == nil {
            silenceStartedAt = now
            return
        }

        guard let silenceStartedAt else { return }
        let silenceDuration = now.timeIntervalSince(silenceStartedAt)
        let recordingDuration =
            now.timeIntervalSince(recordingStartedAt ?? now)
        guard
            silenceDuration >= SilenceDetectionConfig.silenceTimeoutSeconds,
            recordingDuration >= SilenceDetectionConfig.minimumRecordingDurationForAutoStopSeconds
        else {
            return
        }

        triggerSilenceAutoStop()
    }

    private func triggerSilenceAutoStop() {
        guard isRecording, autoStopTask == nil, !isFinalizing else { return }
        let recordingDurationMs = recordingStartedAt.map { voiceElapsedMilliseconds(since: $0) } ?? 0
        logger.info(
            "Detected silence; auto-stopping recording | recordingDurationMs=\(recordingDurationMs)"
        )

        autoStopTask = Task { [weak self] in
            guard let self else { return }
            defer { self.autoStopTask = nil }
            do {
                _ = try await self.finalizeRecordingAndTranscribe(stopReason: .silenceAutoStop)
            } catch {
                self.onError?(error.localizedDescription)
            }
        }
    }

    private func resetSilenceDetectionState() {
        hasDetectedSpeech = false
        silenceStartedAt = nil
        ambientPeakDb = -80
        speechThresholdDb = SilenceDetectionConfig.minimumSpeechThresholdDb
        silenceThresholdDb =
            SilenceDetectionConfig.minimumSpeechThresholdDb - SilenceDetectionConfig.silenceHysteresisDb
    }

    private func transcribeAudio(fileURL: URL) async throws -> String {
        let startedAt = Date()
        let audioSizeBytes = fileSizeBytes(at: fileURL)
        logger.info(
            "Voice transcription request started | bytes=\(audioSizeBytes)"
        )
        do {
            let transcriptionResponse = try await withTranscriptionDeadline(seconds: 60) {
                try await self.openAIService.transcribeAudio(fileURL: fileURL)
            }
            logger.info(
                "Voice transcription request completed | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) transcriptChars=\(transcriptionResponse.text.count)"
            )
            return transcriptionResponse.text
        } catch VoiceDictationError.transcriptionTimedOut {
            logger.error(
                "Voice transcription request timed out | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) bytes=\(audioSizeBytes)"
            )
            throw VoiceDictationError.transcriptionTimedOut
        } catch let error as OpenAIServiceError {
            logger.error(
                "Voice transcription OpenAI service error | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) bytes=\(audioSizeBytes) error=\(error.localizedDescription, privacy: .public)"
            )
            switch error {
            case .notAuthenticated:
                throw VoiceDictationError.notAuthenticated
            case .invalidResponse, .serverError:
                throw VoiceDictationError.transcriptionFailed(error.localizedDescription)
            }
        } catch let apiError as APIError {
            logger.error(
                "Voice transcription API error | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) bytes=\(audioSizeBytes) error=\(apiError.localizedDescription, privacy: .public)"
            )
            throw VoiceDictationError.transcriptionFailed(apiError.localizedDescription)
        } catch {
            logger.error(
                "Voice transcription unexpected error | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) bytes=\(audioSizeBytes) error=\(error.localizedDescription, privacy: .public)"
            )
            throw VoiceDictationError.transcriptionFailed(error.localizedDescription)
        }
    }

    private func fileSizeBytes(at url: URL) -> Int {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attributes[.size] as? NSNumber else {
            return 0
        }
        return size.intValue
    }

    private func observeAudioNotifications() {
        removeAudioNotificationObservers()
        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard
                let typeValue = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                AVAudioSession.InterruptionType(rawValue: typeValue) == .began
            else { return }

            Task { @MainActor in
                self?.cancelRecordingWithMessage("Recording paused (interruption)")
            }
        }
        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor in
                self?.handleAudioRouteChange(notification)
            }
        }
    }

    private func removeAudioNotificationObservers() {
        if let interruptionObserver {
            NotificationCenter.default.removeObserver(interruptionObserver)
            self.interruptionObserver = nil
        }
        if let routeChangeObserver {
            NotificationCenter.default.removeObserver(routeChangeObserver)
            self.routeChangeObserver = nil
        }
    }

    private func cancelRecordingWithMessage(_ message: String) {
        cancelRecording()
        onError?(message)
    }

    private func handleAudioRouteChange(_ notification: Notification) {
        guard isRecording || isTranscribing else { return }
        guard
            let reasonValue = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
            let reason = AVAudioSession.RouteChangeReason(rawValue: reasonValue)
        else {
            logger.debug("Ignoring audio route change without a reason")
            return
        }

        switch reason {
        case .oldDeviceUnavailable:
            guard AVAudioSession.sharedInstance().currentRoute.inputs.isEmpty else {
                logger.debug("Ignoring audio route change because an input route is still available")
                return
            }
            cancelRecordingWithMessage("Recording stopped because the microphone became unavailable")
        case .noSuitableRouteForCategory:
            cancelRecordingWithMessage("Recording stopped because no microphone route is available")
        default:
            logger.debug("Ignoring non-fatal audio route change | reason=\(reason.rawValue)")
        }
    }

    private func withTranscriptionDeadline<T: Sendable>(
        seconds: TimeInterval,
        operation: @escaping @Sendable () async throws -> T
    ) async throws -> T {
        try await withThrowingTaskGroup(of: T.self) { group in
            group.addTask {
                try await operation()
            }
            group.addTask {
                try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
                throw VoiceDictationError.transcriptionTimedOut
            }
            guard let result = try await group.next() else {
                throw VoiceDictationError.transcriptionTimedOut
            }
            group.cancelAll()
            return result
        }
    }
}

// MARK: - AVAudioRecorderDelegate

extension VoiceDictationService: AVAudioRecorderDelegate {
    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in
            if !flag {
                logger.error("Recording did not finish successfully")
            }
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor in
            if let error = error {
                logger.error("Recording encode error: \(error.localizedDescription)")
            }
        }
    }
}
