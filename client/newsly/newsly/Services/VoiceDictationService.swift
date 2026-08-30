
//
//  VoiceDictationService.swift
//  newsly
//
//  Voice dictation service using authenticated backend transcription APIs.
//

import AVFoundation
import Foundation
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

struct VoiceMeteringState {
    enum Action: Equatable {
        case none
        case noSpeechTimeout
        case automaticStop(SpeechStopReason)
    }

    private(set) var hasDetectedSpeech = false
    private(set) var ambientPeakDb: Float = -80
    private(set) var speechThresholdDb = SilenceDetectionConfig.minimumSpeechThresholdDb
    private(set) var silenceThresholdDb =
        SilenceDetectionConfig.minimumSpeechThresholdDb - SilenceDetectionConfig.silenceHysteresisDb

    private var silenceStartedAtRecordingDuration: TimeInterval?

    init() {}

    mutating func observe(
        powerDb: Float,
        recordingDuration: TimeInterval,
        deadlines: SpeechRecordingDeadlines
    ) -> Action {
        if recordingDuration >= deadlines.maximumDurationSeconds {
            return .automaticStop(.maximumDuration)
        }

        // Compare with the threshold from the preceding samples before updating
        // calibration. This lets speech that starts immediately establish itself
        // instead of being reclassified as ambient noise.
        if powerDb >= speechThresholdDb {
            hasDetectedSpeech = true
            silenceStartedAtRecordingDuration = nil
            return .none
        }

        if !hasDetectedSpeech,
           recordingDuration <= SilenceDetectionConfig.calibrationWindowSeconds {
            ambientPeakDb = max(ambientPeakDb, powerDb)
            speechThresholdDb = max(
                ambientPeakDb + SilenceDetectionConfig.speechMarginDb,
                SilenceDetectionConfig.minimumSpeechThresholdDb
            )
            silenceThresholdDb = speechThresholdDb - SilenceDetectionConfig.silenceHysteresisDb
        }

        if hasDetectedSpeech, powerDb >= silenceThresholdDb {
            silenceStartedAtRecordingDuration = nil
            return .none
        }

        guard hasDetectedSpeech else {
            if recordingDuration >= deadlines.noSpeechTimeoutSeconds {
                return .noSpeechTimeout
            }
            return .none
        }

        guard let silenceStartedAtRecordingDuration else {
            self.silenceStartedAtRecordingDuration = recordingDuration
            return .none
        }

        let silenceDuration = recordingDuration - silenceStartedAtRecordingDuration
        guard
            silenceDuration >= SilenceDetectionConfig.silenceTimeoutSeconds,
            recordingDuration >= SilenceDetectionConfig.minimumRecordingDurationForAutoStopSeconds
        else {
            return .none
        }
        return .automaticStop(.silenceAutoStop)
    }
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

/// Service for voice dictation using the authenticated backend transcription API.
@MainActor
final class VoiceDictationService: NSObject, SpeechTranscribing {
    static let shared = VoiceDictationService()

    private struct ActiveSession {
        let id: UUID
        let continuation: AsyncStream<SpeechTranscriptionEvent>.Continuation
        let deadlines: SpeechRecordingDeadlines
    }

    private struct RecordingContext {
        let sessionID: UUID
        let recorder: AVAudioRecorder
        let url: URL
    }

    private var recordingSessionID: UUID?
    private var transcribingSessionID: UUID?
    private var finalizingSessionID: UUID?

    private var isRecording: Bool { recordingSessionID != nil }
    private var isFinalizing: Bool { finalizingSessionID != nil }

    private var activeSession: ActiveSession?

    private var recordingContext: RecordingContext?

    private var meteringTimer: Timer?

    private var autoStopTask: Task<Void, Never>?
    private var autoStopSessionID: UUID?

    private var finalizationTasks: [UUID: Task<String, Error>] = [:]

    private var recordingStartedAt: Date?

    private var meteringState = VoiceMeteringState()

    private var interruptionObserver: NSObjectProtocol?

    private var routeChangeObserver: NSObjectProtocol?

    private var backgroundObserver: NSObjectProtocol?
    private var audioNotificationSessionID: UUID?

    private let audioSessionLease = AudioRecordingSessionLease()

    private let openAIService = OpenAIService.shared

    private let testTranscriptionOperation: (@MainActor (URL) async throws -> String)?

    private override init() {
        testTranscriptionOperation = nil
        super.init()
    }

    #if DEBUG
    init(
        testTranscriptionOperation: @escaping @MainActor (URL) async throws -> String
    ) {
        self.testTranscriptionOperation = testTranscriptionOperation
        super.init()
    }

    func prepareRecordingForTesting(
        sessionID: UUID,
        recorder: AVAudioRecorder
    ) throws {
        guard activeSession?.id == sessionID else {
            throw VoiceDictationError.noActiveSession
        }
        recorder.delegate = self
        recordingContext = RecordingContext(
            sessionID: sessionID,
            recorder: recorder,
            url: recorder.url
        )
        recordingSessionID = sessionID
        recordingStartedAt = Date()
        resetSilenceDetectionState()
        observeAudioNotifications(sessionID: sessionID)
    }

    func triggerAutomaticStopForTesting(reason: SpeechStopReason) {
        triggerAutomaticStop(reason: reason)
    }
    #endif

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

    func makeSession(
        deadlines: SpeechRecordingDeadlines
    ) throws -> SpeechTranscriptionSession {
        guard activeSession == nil else { throw VoiceDictationError.sessionBusy }

        let sessionID = UUID()
        let pair = AsyncStream<SpeechTranscriptionEvent>.makeStream()
        activeSession = ActiveSession(
            id: sessionID,
            continuation: pair.continuation,
            deadlines: deadlines
        )
        return SpeechTranscriptionSession(
            id: sessionID,
            events: pair.stream,
            start: { [weak self] id in
                guard let self else { throw VoiceDictationError.noActiveSession }
                do {
                    try await self.startRecording(sessionID: id)
                } catch {
                    self.cancelRecording(sessionID: id, notifyStopReason: false)
                    throw error
                }
            },
            stop: { [weak self] id in
                guard let self else { throw VoiceDictationError.noActiveSession }
                return try await self.finalizeRecordingAndTranscribe(
                    stopReason: .manual,
                    sessionID: id
                )
            },
            cancel: { [weak self] id in
                self?.cancelRecording(sessionID: id, notifyStopReason: true)
            }
        )
    }

    /// Start recording audio.
    private func startRecording(sessionID: UUID) async throws {
        let startedAt = Date()
        guard activeSession?.id == sessionID else { throw VoiceDictationError.noActiveSession }
        if !isAvailable {
            _ = await openAIService.refreshTranscriptionAvailability()
        }
        guard activeSession?.id == sessionID else { throw VoiceDictationError.noActiveSession }
        guard isAvailable else {
            logger.error(
                "Voice recording unavailable | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt))"
            )
            throw VoiceDictationError.notAuthenticated
        }

        let hasPermission = await requestMicrophonePermission()
        guard activeSession?.id == sessionID else { throw VoiceDictationError.noActiveSession }
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
        } catch {
            audioSessionLease.deactivate()
            logger.error(
                "Voice recording audio session failed | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw VoiceDictationError.audioSessionError(error)
        }

        // Create recording URL
        let documentsPath = FileManager.default.temporaryDirectory
        let audioFilename = documentsPath.appendingPathComponent(
            "voice_dictation_\(sessionID.uuidString).m4a"
        )
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

            recordingContext = RecordingContext(
                sessionID: sessionID,
                recorder: recorder,
                url: audioFilename
            )
            startMetering()
            recordingSessionID = sessionID
            observeAudioNotifications(sessionID: sessionID)
            logger.info(
                "Started recording | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) sampleRate=16000 channels=1"
            )
        } catch {
            audioSessionLease.deactivate()
            removeAudioNotificationObservers(sessionID: sessionID)
            logger.error(
                "Voice recording start failed | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) error=\(error.localizedDescription, privacy: .public)"
            )
            throw VoiceDictationError.recordingFailed
        }
    }

    private func cancelRecording(
        sessionID: UUID,
        notifyStopReason: Bool,
        failure: (message: String, reason: SpeechStopReason)? = nil
    ) {
        guard activeSession?.id == sessionID else { return }
        let startedAt = Date()
        let context = recordingContext?.sessionID == sessionID ? recordingContext : nil
        let wasActive = recordingSessionID == sessionID
            || transcribingSessionID == sessionID
            || context != nil
        stopMetering()
        if autoStopSessionID == sessionID {
            autoStopTask?.cancel()
            autoStopTask = nil
            autoStopSessionID = nil
        }
        finalizationTasks[sessionID]?.cancel()
        if let context {
            recordingContext = nil
            context.recorder.delegate = nil
            context.recorder.stop()
            try? FileManager.default.removeItem(at: context.url)
        }
        audioSessionLease.deactivate()
        removeAudioNotificationObservers(sessionID: sessionID)
        if recordingSessionID == sessionID {
            recordingSessionID = nil
        }
        if transcribingSessionID == sessionID {
            transcribingSessionID = nil
        }
        if finalizingSessionID == sessionID {
            finalizingSessionID = nil
        }
        resetSilenceDetectionState()
        recordingStartedAt = nil

        if let failure {
            emit(.stateChange(.failed(failure.message)), for: sessionID)
            emit(.error(failure.message), for: sessionID)
            emit(.stopReason(failure.reason), for: sessionID)
        } else if wasActive, notifyStopReason {
            emit(.stateChange(.idle), for: sessionID)
            emit(.stopReason(.cancel), for: sessionID)
        }
        if wasActive {
            logger.info(
                "Voice recording cancelled | elapsedMs=\(voiceElapsedMilliseconds(since: startedAt)) notifyStopReason=\(notifyStopReason)"
            )
        }
        releaseSession(sessionID: sessionID)
    }

    // MARK: - Private

    private func emit(_ event: SpeechTranscriptionEvent, for sessionID: UUID) {
        guard activeSession?.id == sessionID else { return }
        activeSession?.continuation.yield(event)
    }

    private func failSession(sessionID: UUID, message: String) {
        guard activeSession?.id == sessionID else { return }
        cancelRecording(
            sessionID: sessionID,
            notifyStopReason: false,
            failure: (message, .failure)
        )
    }

    private func releaseSession(sessionID: UUID) {
        guard activeSession?.id == sessionID else { return }
        activeSession?.continuation.finish()
        activeSession = nil
    }

    private func finalizeRecordingAndTranscribe(
        stopReason: SpeechStopReason,
        sessionID: UUID
    ) async throws -> String {
        let finalizeStartedAt = Date()
        guard activeSession?.id == sessionID else {
            throw VoiceDictationError.noActiveSession
        }
        if finalizingSessionID == sessionID,
           let existingFinalization = finalizationTasks[sessionID] {
            return try await existingFinalization.value
        }
        guard recordingSessionID == sessionID,
              let context = recordingContext,
              context.sessionID == sessionID else {
            throw VoiceDictationError.recordingFailed
        }
        guard !isFinalizing else {
            throw VoiceDictationError.recordingFailed
        }

        finalizingSessionID = sessionID
        let recordingDurationMs = recordingStartedAt.map { voiceElapsedMilliseconds(since: $0) } ?? 0
        stopMetering()
        context.recorder.stop()
        removeAudioNotificationObservers(sessionID: sessionID)
        audioSessionLease.deactivate()
        recordingSessionID = nil
        recordingStartedAt = nil
        resetSilenceDetectionState()
        logger.info(
            "Stopped recording | stopReason=\(String(describing: stopReason), privacy: .public) finalizeElapsedMs=\(voiceElapsedMilliseconds(since: finalizeStartedAt)) recordingDurationMs=\(recordingDurationMs)"
        )

        let url = context.url
        let audioSizeBytes = fileSizeBytes(at: url)
        logger.info(
            "Voice recording ready for transcription | stopReason=\(String(describing: stopReason), privacy: .public) bytes=\(audioSizeBytes) recordingDurationMs=\(recordingDurationMs)"
        )

        transcribingSessionID = sessionID
        emit(.stateChange(.transcribing), for: sessionID)
        let transcriptionTask = Task { [weak self] in
            guard let self else { throw VoiceDictationError.noActiveSession }
            return try await self.transcribeAudio(fileURL: url)
        }
        finalizationTasks[sessionID] = transcriptionTask
        defer {
            finalizationTasks[sessionID] = nil
            if transcribingSessionID == sessionID {
                transcribingSessionID = nil
            }
            if finalizingSessionID == sessionID {
                finalizingSessionID = nil
            }
            if let currentContext = recordingContext,
               currentContext.sessionID == sessionID,
               currentContext.recorder === context.recorder {
                currentContext.recorder.delegate = nil
                recordingContext = nil
            }
            removeAudioNotificationObservers(sessionID: sessionID)
            try? FileManager.default.removeItem(at: url)
        }

        do {
            let transcript = try await transcriptionTask.value
            emit(.transcriptFinal(transcript), for: sessionID)
            emit(.stateChange(.idle), for: sessionID)
            emit(.stopReason(stopReason), for: sessionID)
            logger.info(
                "Voice recording finalized | stopReason=\(String(describing: stopReason), privacy: .public) totalElapsedMs=\(voiceElapsedMilliseconds(since: finalizeStartedAt)) transcriptChars=\(transcript.count)"
            )
            releaseSession(sessionID: sessionID)
            return transcript
        } catch {
            failSession(sessionID: sessionID, message: error.localizedDescription)
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
        guard let sessionID = activeSession?.id,
              recordingSessionID == sessionID,
              !isFinalizing,
              let context = recordingContext,
              context.sessionID == sessionID else { return }
        let recorder = context.recorder
        recorder.updateMeters()

        let powerDb = recorder.averagePower(forChannel: 0)
        let now = Date()
        let recordingDuration = now.timeIntervalSince(recordingStartedAt ?? now)
        guard let deadlines = activeSession?.deadlines else { return }
        switch meteringState.observe(
            powerDb: powerDb,
            recordingDuration: recordingDuration,
            deadlines: deadlines
        ) {
        case .none:
            return
        case .noSpeechTimeout:
            stopForNoSpeech()
        case .automaticStop(let reason):
            triggerAutomaticStop(reason: reason)
        }
    }

    private func triggerAutomaticStop(reason: SpeechStopReason) {
        guard isRecording,
              autoStopTask == nil,
              !isFinalizing,
              let sessionID = activeSession?.id else { return }
        let recordingDurationMs = recordingStartedAt.map { voiceElapsedMilliseconds(since: $0) } ?? 0
        logger.info(
            "Auto-stopping voice recording | reason=\(String(describing: reason), privacy: .public) recordingDurationMs=\(recordingDurationMs)"
        )

        autoStopSessionID = sessionID
        autoStopTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.autoStopSessionID == sessionID {
                    self.autoStopTask = nil
                    self.autoStopSessionID = nil
                }
            }
            do {
                _ = try await self.finalizeRecordingAndTranscribe(
                    stopReason: reason,
                    sessionID: sessionID
                )
            } catch {
                // finalizeRecordingAndTranscribe publishes the failure to the owner.
            }
        }
    }

    private func stopForNoSpeech() {
        guard let sessionID = activeSession?.id else { return }
        let message = "No speech detected. Try again."
        cancelRecording(
            sessionID: sessionID,
            notifyStopReason: false,
            failure: (message, .noSpeechTimeout)
        )
        logger.info("Voice recording stopped without detected speech")
    }

    private func resetSilenceDetectionState() {
        meteringState = VoiceMeteringState()
    }

    private func transcribeAudio(fileURL: URL) async throws -> String {
        if let testTranscriptionOperation {
            return try await testTranscriptionOperation(fileURL)
        }
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

    private func observeAudioNotifications(sessionID: UUID) {
        removeAudioNotificationObservers()
        audioNotificationSessionID = sessionID
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
                self?.cancelRecordingWithMessage(
                    "Recording paused (interruption)",
                    sessionID: sessionID
                )
            }
        }
        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor in
                self?.handleAudioRouteChange(notification, sessionID: sessionID)
            }
        }
        backgroundObserver = NotificationCenter.default.addObserver(
            forName: speechAppDidEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.cancelRecordingWithMessage(
                    "Recording stopped when the app moved to the background",
                    sessionID: sessionID
                )
            }
        }
    }

    private func removeAudioNotificationObservers(sessionID: UUID? = nil) {
        guard sessionID == nil || audioNotificationSessionID == sessionID else { return }
        if let interruptionObserver {
            NotificationCenter.default.removeObserver(interruptionObserver)
            self.interruptionObserver = nil
        }
        if let routeChangeObserver {
            NotificationCenter.default.removeObserver(routeChangeObserver)
            self.routeChangeObserver = nil
        }
        if let backgroundObserver {
            NotificationCenter.default.removeObserver(backgroundObserver)
            self.backgroundObserver = nil
        }
        audioNotificationSessionID = nil
    }

    private func cancelRecordingWithMessage(_ message: String, sessionID: UUID) {
        guard activeSession?.id == sessionID,
              recordingSessionID == sessionID,
              audioNotificationSessionID == sessionID else { return }
        cancelRecording(
            sessionID: sessionID,
            notifyStopReason: false,
            failure: (message, .failure)
        )
    }

    private func handleAudioRouteChange(_ notification: Notification, sessionID: UUID) {
        guard activeSession?.id == sessionID,
              recordingSessionID == sessionID,
              audioNotificationSessionID == sessionID else { return }
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
            cancelRecordingWithMessage(
                "Recording stopped because the microphone became unavailable",
                sessionID: sessionID
            )
        case .noSuitableRouteForCategory:
            cancelRecordingWithMessage(
                "Recording stopped because no microphone route is available",
                sessionID: sessionID
            )
        default:
            logger.debug("Ignoring non-fatal audio route change | reason=\(reason.rawValue)")
        }
    }

    private func handleRecorderFailure(
        _ recorder: AVAudioRecorder,
        message: String
    ) {
        guard let context = recordingContext,
              context.recorder === recorder,
              activeSession?.id == context.sessionID else {
            logger.debug("Ignoring stale audio recorder failure callback")
            return
        }
        logger.error("Audio recorder failed: \(message, privacy: .public)")
        failSession(sessionID: context.sessionID, message: message)
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
        guard !flag else { return }
        Task { @MainActor in
            self.handleRecorderFailure(
                recorder,
                message: "Recording did not finish successfully."
            )
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor in
            let detail = error?.localizedDescription ?? "Unknown audio encoding error."
            self.handleRecorderFailure(
                recorder,
                message: "Recording encode error: \(detail)"
            )
        }
    }
}
