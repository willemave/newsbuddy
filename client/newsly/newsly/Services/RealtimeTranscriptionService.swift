import AVFoundation
import Foundation
import os.log

private let realtimeLogger = Logger(
    subsystem: Bundle.main.bundleIdentifier ?? "org.willemaw.newsly",
    category: "RealtimeTranscription"
)

enum RealtimeTranscriptionError: LocalizedError {
    case noMicrophoneAccess
    case audioSessionError(String)
    case connectionFailed
    case connectionTimeout
    case tokenMissing

    var errorDescription: String? {
        switch self {
        case .noMicrophoneAccess:
            return "Microphone access denied"
        case .audioSessionError(let message):
            return "Audio session error: \(message)"
        case .connectionFailed:
            return "Realtime connection failed"
        case .connectionTimeout:
            return "Realtime connection timed out"
        case .tokenMissing:
            return "Realtime token unavailable"
        }
    }
}

private enum RealtimeConnectionState {
    case idle
    case connecting
    case connected
    case failed
}

@MainActor
final class RealtimeTranscriptionService: SpeechTranscribing, @unchecked Sendable {
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptFinal: ((String) -> Void)?
    var onError: ((String) -> Void)?
    var onStateChange: ((SpeechTranscriptionState) -> Void)?
    var onStopReason: ((SpeechStopReason) -> Void)?

    private let openAIService = OpenAIService.shared
    private let audioQueue = DispatchQueue(label: "com.newsly.realtime.audio")
    private let defaultModel = "gpt-realtime"
    private let transcriptionModel = "gpt-4o-mini-transcribe"
    private let targetSampleRate: Double = 24_000
    private let connectionTimeoutSeconds: TimeInterval = 5

    private var webSocket: URLSessionWebSocketTask?
    private var audioEngine: AVAudioEngine?
    private var audioConverter: AVAudioConverter?
    private var currentTranscript: String = ""
    private(set) var isRecording = false {
        didSet { notifyStateChange() }
    }
    private(set) var isTranscribing = false {
        didSet { notifyStateChange() }
    }
    private var connectionState: RealtimeConnectionState = .idle
    private var pendingEvents: [[String: Any]] = []
    private var connectionContinuation: CheckedContinuation<Void, Error>?
    private var finalTranscriptContinuation: CheckedContinuation<String, Error>?
    private var totalSamplesSent: Int64 = 0
    private var activeSessionType: String?
    private var hasFinalTranscript = false
    private let finalTranscriptTimeoutSeconds: TimeInterval = 1.2
    private var isClosingConnection = false

    func requestMicrophonePermission() async -> Bool {
        realtimeLogger.info("Requesting microphone permission")
        return await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { granted in
                realtimeLogger.info("Microphone permission granted: \(granted, privacy: .public)")
                continuation.resume(returning: granted)
            }
        }
    }

    func start() async throws {
        guard !isRecording else { return }
        realtimeLogger.info("Starting realtime transcription")
        currentTranscript = ""
        isClosingConnection = false

        let hasPermission = await requestMicrophonePermission()
        guard hasPermission else {
            realtimeLogger.error("Microphone permission denied")
            throw RealtimeTranscriptionError.noMicrophoneAccess
        }

        try configureAudioSession()
        realtimeLogger.info("Fetching realtime token")
        let tokenResponse = try await openAIService.fetchRealtimeToken()
        let resolvedModel = resolveModel(tokenResponse.model)
        activeSessionType = tokenResponse.sessionType
        realtimeLogger.info("Realtime token fetched with model: \(resolvedModel, privacy: .public)")
        let token = tokenResponse.token
        guard !token.isEmpty else {
            realtimeLogger.error("Realtime token missing")
            throw RealtimeTranscriptionError.tokenMissing
        }

        realtimeLogger.info("Connecting to realtime websocket")
        try await connect(token: token, model: resolvedModel)
        startAudioEngine()
        isRecording = true
        isTranscribing = true
        totalSamplesSent = 0
        hasFinalTranscript = false
        do {
            try await waitForConnection()
        } catch {
            reset()
            throw error
        }
        realtimeLogger.info("Realtime transcription started")
    }

    func stop() async throws -> String {
        guard isRecording else { return currentTranscript }
        realtimeLogger.info("Stopping realtime transcription")
        stopAudioEngine()
        let audioDurationMs = (Double(totalSamplesSent) / targetSampleRate) * 1000
        let shouldCommit = audioDurationMs >= 100
        let shouldAwaitFinal = shouldCommit && !hasFinalTranscript
        if activeSessionType == "transcription" {
            if shouldAwaitFinal {
                realtimeLogger.info("Sending commit for transcription session")
                sendEvent(["type": "input_audio_buffer.commit"])
            } else {
                realtimeLogger.info(
                    "Skipping commit; transcription session already finalized or buffer too small"
                )
            }
        } else if shouldCommit {
            sendEvent(["type": "input_audio_buffer.commit"])
        } else {
            realtimeLogger.info(
                "Skipping commit; audio buffer too small (\(audioDurationMs, privacy: .public)ms)"
            )
        }

        if shouldAwaitFinal {
            do {
                _ = try await waitForFinalTranscript()
            } catch {
                realtimeLogger.error(
                    "Timed out waiting for final realtime transcript: \(error.localizedDescription, privacy: .public)"
                )
            }
        }

        isClosingConnection = true
        webSocket?.cancel(with: .normalClosure, reason: nil)
        isRecording = false
        isTranscribing = false
        connectionState = .idle
        pendingEvents.removeAll()
        totalSamplesSent = 0
        activeSessionType = nil
        hasFinalTranscript = false
        realtimeLogger.info("Realtime transcription stopped")
        onStopReason?(.manual)
        return currentTranscript
    }

    func reset() {
        realtimeLogger.info("Resetting realtime transcription")
        stopAudioEngine()
        webSocket?.cancel(with: .goingAway, reason: nil)
        currentTranscript = ""
        isClosingConnection = true
        isRecording = false
        isTranscribing = false
        connectionState = .idle
        pendingEvents.removeAll()
        connectionContinuation = nil
        finalTranscriptContinuation = nil
        totalSamplesSent = 0
        activeSessionType = nil
        hasFinalTranscript = false
    }

    func cancel() {
        reset()
        onStopReason?(.cancel)
    }

    // MARK: - Private

    private func configureAudioSession() throws {
        let audioSession = AVAudioSession.sharedInstance()
        realtimeLogger.info("Configuring audio session")
        do {
            try audioSession.setCategory(
                .playAndRecord,
                mode: .measurement,
                options: [.defaultToSpeaker, .allowBluetoothHFP]
            )
            try audioSession.setPreferredSampleRate(targetSampleRate)
            try audioSession.setPreferredIOBufferDuration(0.02)
            try audioSession.setActive(true)
            realtimeLogger.info("Audio session configured")
        } catch {
            realtimeLogger.error("Audio session configuration failed: \(error.localizedDescription, privacy: .public)")
            throw RealtimeTranscriptionError.audioSessionError(error.localizedDescription)
        }
    }

    private func resolveModel(_ model: String?) -> String {
        let trimmedModel = model?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (trimmedModel?.isEmpty == false ? trimmedModel : nil) ?? defaultModel
    }

    private func connect(token: String, model: String?) async throws {
        connectionState = .connecting
        let modelValue = resolveModel(model)
        realtimeLogger.info("Preparing websocket connection for model: \(modelValue, privacy: .public)")
        guard var components = URLComponents(string: "wss://api.openai.com/v1/realtime") else {
            throw RealtimeTranscriptionError.connectionFailed
        }
        if activeSessionType != "transcription" {
            components.queryItems = [URLQueryItem(name: "model", value: modelValue)]
        } else {
            realtimeLogger.info("Transcription session detected; omitting model query parameter")
        }
        guard let url = components.url else {
            throw RealtimeTranscriptionError.connectionFailed
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if activeSessionType == "transcription" {
            request.setValue("realtime=v1", forHTTPHeaderField: "OpenAI-Beta")
            realtimeLogger.info("Using Realtime beta header for transcription session")
        }

        let task = URLSession.shared.webSocketTask(with: request)
        task.resume()
        webSocket = task
        listenForMessages()
        realtimeLogger.info("WebSocket task started")
    }

    private func waitForConnection() async throws {
        if connectionState == .connected {
            return
        }
        realtimeLogger.info("Waiting for realtime session handshake")
        try await withCheckedThrowingContinuation { continuation in
            if connectionState == .connected {
                continuation.resume(returning: ())
                return
            }
            connectionContinuation = continuation
            scheduleConnectionTimeout()
        }
    }

    private func scheduleConnectionTimeout() {
        let timeoutSeconds = connectionTimeoutSeconds
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
            guard let self else { return }
            if self.connectionState != .connected {
                self.connectionState = .failed
                realtimeLogger.error("Realtime connection timeout")
                self.resumeConnectionFailure(RealtimeTranscriptionError.connectionTimeout)
            }
        }
    }

    private func listenForMessages() {
        webSocket?.receive { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                switch result {
                case .failure(let error):
                    if self.isClosingConnection || self.connectionState == .idle {
                        realtimeLogger.debug(
                            "Ignoring websocket receive error during shutdown: \(error.localizedDescription, privacy: .public)"
                        )
                        return
                    }
                    realtimeLogger.error(
                        "WebSocket receive error: \(error.localizedDescription, privacy: .public)"
                    )
                    self.connectionState = .failed
                    self.resumeConnectionFailure(error)
                    self.onError?(error.localizedDescription)
                case .success(let message):
                    realtimeLogger.debug("WebSocket message received")
                    self.handleMessage(message)
                    self.listenForMessages()
                }
            }
        }
    }

    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        let textPayload: String?
        switch message {
        case .string(let text):
            textPayload = text
        case .data(let data):
            textPayload = String(data: data, encoding: .utf8)
        @unknown default:
            textPayload = nil
        }

        guard let textPayload,
              let data = textPayload.data(using: .utf8),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let type = json["type"] as? String else {
            return
        }

        if let errorPayload = json["error"] as? [String: Any],
           let message = errorPayload["message"] as? String {
            realtimeLogger.error("Realtime error payload: \(message, privacy: .public)")
            connectionState = .failed
            resumeConnectionFailure(RealtimeTranscriptionError.connectionFailed)
            onError?(message)
            return
        }

        if type == "session.created"
            || type == "session.updated"
            || type == "transcription_session.created"
            || type == "transcription_session.updated"
        {
            connectionState = .connected
            realtimeLogger.info("Realtime session ready: \(type, privacy: .public)")
            flushPendingEvents()
            resumeConnectionSuccess()
        }

        if type == "response.output_text.delta" || type == "response.text.delta",
           let delta = json["delta"] as? String {
            currentTranscript += delta
            onTranscriptDelta?(delta)
            return
        }

        if type == "response.output_text.done" || type == "response.text.done",
           let text = json["text"] as? String {
            currentTranscript = text
            hasFinalTranscript = true
            resumeFinalTranscript(with: text)
            onTranscriptFinal?(text)
            return
        }

        if type == "conversation.item.input_audio_transcription.delta",
           let delta = json["delta"] as? String {
            currentTranscript += delta
            onTranscriptDelta?(delta)
            return
        }

        if type == "conversation.item.input_audio_transcription.completed",
           let transcript = json["transcript"] as? String {
            currentTranscript = transcript
            hasFinalTranscript = true
            resumeFinalTranscript(with: transcript)
            onTranscriptFinal?(transcript)
            return
        }

        if type.hasSuffix(".delta"), let delta = json["delta"] as? String {
            currentTranscript += delta
            onTranscriptDelta?(delta)
            return
        }

        if let text = json["text"] as? String {
            currentTranscript = text
            resumeFinalTranscript(with: text)
            onTranscriptFinal?(text)
            return
        }

        if let transcript = json["transcript"] as? String {
            currentTranscript = transcript
            resumeFinalTranscript(with: transcript)
            onTranscriptFinal?(transcript)
        }
    }

    private func startAudioEngine() {
        let audioEngine = AVAudioEngine()
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        guard let targetFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: targetSampleRate, channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: inputFormat, to: targetFormat) else {
            realtimeLogger.error("Failed to configure audio converter")
            return
        }

        audioConverter = converter
        self.audioEngine = audioEngine

        let audioQueue = audioQueue
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            audioQueue.async {
                Task { @MainActor in
                    self?.sendAudioBuffer(buffer, targetFormat: targetFormat)
                }
            }
        }

        do {
            try audioEngine.start()
            realtimeLogger.info("Audio engine started")
        } catch {
            realtimeLogger.error("Failed to start audio engine: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func stopAudioEngine() {
        realtimeLogger.info("Stopping audio engine")
        audioEngine?.inputNode.removeTap(onBus: 0)
        audioEngine?.stop()
        audioEngine = nil
        audioConverter = nil
    }

    private func sendAudioBuffer(_ buffer: AVAudioPCMBuffer, targetFormat: AVAudioFormat) {
        guard let converter = audioConverter else { return }
        guard let outputBuffer = AVAudioPCMBuffer(
            pcmFormat: targetFormat,
            frameCapacity: AVAudioFrameCount(buffer.frameLength)
        ) else {
            return
        }

        var error: NSError?
        let inputBlock: AVAudioConverterInputBlock = { _, outStatus in
            outStatus.pointee = .haveData
            return buffer
        }

        converter.convert(to: outputBuffer, error: &error, withInputFrom: inputBlock)
        if let error {
            realtimeLogger.error("Audio conversion error: \(error.localizedDescription, privacy: .public)")
            return
        }

        let data: Data
        if let channelData = outputBuffer.int16ChannelData {
            data = Data(
                bytes: channelData[0],
                count: Int(outputBuffer.frameLength) * MemoryLayout<Int16>.size
            )
        } else {
            let audioBuffer = outputBuffer.audioBufferList.pointee.mBuffers
            guard let mData = audioBuffer.mData else { return }
            data = Data(bytes: mData, count: Int(audioBuffer.mDataByteSize))
        }

        let base64Audio = data.base64EncodedString()
        totalSamplesSent += Int64(outputBuffer.frameLength)
        sendEvent(["type": "input_audio_buffer.append", "audio": base64Audio])
    }

    private func sendEvent(_ event: [String: Any], requiresConnection: Bool = true) {
        guard let webSocket else { return }
        if requiresConnection && connectionState != .connected {
            if let eventType = event["type"] as? String, eventType != "input_audio_buffer.append" {
                realtimeLogger.debug("Queueing event while connecting: \(eventType, privacy: .public)")
            }
            pendingEvents.append(event)
            return
        }
        guard let data = try? JSONSerialization.data(withJSONObject: event),
              let text = String(data: data, encoding: .utf8) else {
            return
        }

        webSocket.send(.string(text)) { [weak self] error in
            guard let error else { return }
            Task { @MainActor in
                guard let self else { return }
                if self.isClosingConnection || self.connectionState == .idle {
                    realtimeLogger.debug(
                        "Ignoring websocket send error during shutdown: \(error.localizedDescription, privacy: .public)"
                    )
                    return
                }
                realtimeLogger.error(
                    "WebSocket send error: \(error.localizedDescription, privacy: .public)"
                )
                self.connectionState = .failed
                self.resumeConnectionFailure(error)
                self.onError?(error.localizedDescription)
            }
        }
    }

    private func flushPendingEvents() {
        guard connectionState == .connected, !pendingEvents.isEmpty else { return }
        let queued = pendingEvents
        pendingEvents.removeAll()
        for event in queued {
            sendEvent(event)
        }
    }

    private func resumeConnectionSuccess() {
        connectionContinuation?.resume(returning: ())
        connectionContinuation = nil
    }

    private func resumeConnectionFailure(_ error: Error) {
        connectionContinuation?.resume(throwing: error)
        connectionContinuation = nil
    }

    private func waitForFinalTranscript() async throws -> String {
        if hasFinalTranscript {
            return currentTranscript
        }

        return try await withCheckedThrowingContinuation { continuation in
            finalTranscriptContinuation = continuation
            Task { @MainActor [weak self] in
                let timeoutSeconds = self?.finalTranscriptTimeoutSeconds ?? 1.2
                try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
                guard let self else { return }
                guard self.finalTranscriptContinuation != nil else { return }
                self.finalTranscriptContinuation?.resume(returning: self.currentTranscript)
                self.finalTranscriptContinuation = nil
            }
        }
    }

    private func resumeFinalTranscript(with transcript: String) {
        finalTranscriptContinuation?.resume(returning: transcript)
        finalTranscriptContinuation = nil
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
