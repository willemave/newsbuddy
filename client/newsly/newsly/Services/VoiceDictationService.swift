
//
//  VoiceDictationService.swift
//  newsly
//
//  Voice dictation service using authenticated backend transcription APIs.
//

import AVFoundation
import Foundation
import os
import UIKit

private let logger = Logger(subsystem: "com.newsly", category: "VoiceDictation")
private let voicePerfSignposter = OSSignposter(subsystem: "com.newsly.chat", category: "perf")

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
final class VoiceDictationService: NSObject, ObservableObject, SpeechTranscribing {
    static let shared = VoiceDictationService()

    @Published private(set) var isRecording = false {
        didSet { notifyStateChange() }
    }
    @Published private(set) var isTranscribing = false {
        didSet { notifyStateChange() }
    }

    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptFinal: ((String) -> Void)?
    var onError: ((String) -> Void)?
    var onStateChange: ((SpeechTranscriptionState) -> Void)?
    var onStopReason: ((SpeechStopReason) -> Void)?

    private var audioRecorder: AVAudioRecorder?
    private var recordingURL: URL?
    private var meteringTimer: Timer?
    private var autoStopTask: Task<Void, Never>?
    private var recordingStartedAt: Date?
    private var silenceStartedAt: Date?
    private var hasDetectedSpeech = false
    private var ambientPeakDb: Float = -80
    private var speechThresholdDb = SilenceDetectionConfig.minimumSpeechThresholdDb
    private var silenceThresholdDb =
        SilenceDetectionConfig.minimumSpeechThresholdDb - SilenceDetectionConfig.silenceHysteresisDb
    private var isFinalizing = false
    private var interruptionObserver: NSObjectProtocol?
    private var routeChangeObserver: NSObjectProtocol?
    private let audioSessionLease = AudioRecordingSessionLease()
    private let openAIService = OpenAIService.shared

    private override init() {
        super.init()
    }

    /// Request microphone permission.
    func requestMicrophonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
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
        if !isAvailable {
            _ = await openAIService.refreshTranscriptionAvailability()
        }
        guard isAvailable else {
            throw VoiceDictationError.notAuthenticated
        }

        let hasPermission = await requestMicrophonePermission()
        guard hasPermission else {
            throw VoiceDictationError.noMicrophoneAccess
        }

        do {
            try audioSessionLease.activate()
            observeAudioNotifications()
        } catch {
            audioSessionLease.deactivate()
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
            playRecordingStartHaptic()
            logger.info("Started recording")
        } catch {
            audioSessionLease.deactivate()
            removeAudioNotificationObservers()
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
        guard isRecording, let recorder = audioRecorder else {
            throw VoiceDictationError.recordingFailed
        }
        guard !isFinalizing else {
            throw VoiceDictationError.recordingFailed
        }

        isFinalizing = true
        stopMetering()
        recorder.stop()
        audioRecorder = nil
        audioSessionLease.deactivate()
        removeAudioNotificationObservers()
        isRecording = false
        playRecordingStopHaptic()
        logger.info("Stopped recording")

        guard let url = recordingURL else {
            isFinalizing = false
            throw VoiceDictationError.recordingFailed
        }

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
            return transcript
        } catch {
            onStopReason?(.failure)
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
        logger.info("Detected silence; auto-stopping recording")

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
        do {
            let transcriptionResponse = try await withTranscriptionDeadline(seconds: 60) {
                try await self.openAIService.transcribeAudio(fileURL: fileURL)
            }
            logger.info("Transcription successful: \(transcriptionResponse.text.prefix(50))...")
            return transcriptionResponse.text
        } catch VoiceDictationError.transcriptionTimedOut {
            throw VoiceDictationError.transcriptionTimedOut
        } catch let error as OpenAIServiceError {
            switch error {
            case .notAuthenticated:
                throw VoiceDictationError.notAuthenticated
            case .invalidResponse, .serverError:
                throw VoiceDictationError.transcriptionFailed(error.localizedDescription)
            }
        } catch let apiError as APIError {
            throw VoiceDictationError.transcriptionFailed(apiError.localizedDescription)
        } catch {
            throw VoiceDictationError.transcriptionFailed(error.localizedDescription)
        }
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
        ) { [weak self] _ in
            Task { @MainActor in
                self?.cancelRecordingWithMessage("Recording stopped because the audio route changed")
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

    private func playRecordingStartHaptic() {
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    private func playRecordingStopHaptic() {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
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
