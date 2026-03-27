//
//  ChatSessionViewModel.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation
import SwiftUI
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ChatSessionViewModel")

@MainActor
class ChatSessionViewModel: ObservableObject {
    @Published var session: ChatSessionSummary?
    @Published var messages: [ChatMessage] = []
    @Published var isLoading = false
    @Published var isSending = false
    @Published var errorMessage: String?
    @Published var inputText: String = ""
    @Published var thinkingElapsedSeconds = 0

    // Voice dictation state
    @Published var isRecording = false
    @Published var isTranscribing = false
    @Published var activeTranscript: String = ""
    @Published private(set) var voiceDictationAvailable = false

    private let chatService = ChatService.shared
    private let transcriptionService: any SpeechTranscribing
    private var thinkingTimer: Timer?
    let sessionId: Int
    private let initialPendingUserMessage: ChatMessage?
    private let initialPendingMessageId: Int?

    init(
        sessionId: Int,
        initialPendingUserMessage: ChatMessage? = nil,
        initialPendingMessageId: Int? = nil,
        transcriptionService: (any SpeechTranscribing)? = nil
    ) {
        self.sessionId = sessionId
        self.initialPendingUserMessage = initialPendingUserMessage
        self.initialPendingMessageId = initialPendingMessageId
        self.messages = initialPendingUserMessage.map { [$0] } ?? []
        let resolvedService = transcriptionService ?? RealtimeTranscriptionService()
        self.transcriptionService = resolvedService
        configureTranscriptionCallbacks()
    }

    init(
        session: ChatSessionSummary,
        initialPendingUserMessage: ChatMessage? = nil,
        initialPendingMessageId: Int? = nil,
        transcriptionService: (any SpeechTranscribing)? = nil
    ) {
        self.sessionId = session.id
        self.session = session
        self.initialPendingUserMessage = initialPendingUserMessage
        self.initialPendingMessageId = initialPendingMessageId
        self.messages = initialPendingUserMessage.map { [$0] } ?? []
        let resolvedService = transcriptionService ?? RealtimeTranscriptionService()
        self.transcriptionService = resolvedService
        configureTranscriptionCallbacks()
    }

    deinit {
        thinkingTimer?.invalidate()
    }

    func loadSession() async {
        logger.debug("[ViewModel] loadSession | sessionId=\(self.sessionId)")
        isLoading = true
        errorMessage = nil
        seedInitialPendingMessageIfNeeded()

        do {
            let detail = try await chatService.getSession(id: sessionId)
            session = detail.session
            let filteredMessages = detail.messages.filter { !$0.content.isEmpty }
            messages = filteredMessages.isEmpty ? (initialPendingUserMessage.map { [$0] } ?? []) : filteredMessages
            let assistantPreview = messages.last(where: { $0.isAssistant })?.content.prefix(160) ?? ""
            logger.debug(
                "[ViewModel] loadSession succeeded | sessionId=\(self.sessionId) messages=\(self.messages.count) assistantPreview=\(String(assistantPreview), privacy: .public)"
            )

            // Check if there's a processing message we need to poll for
            if let processingMessage = filteredMessages.first(where: { $0.isProcessing }) {
                let pollingMessageId = processingMessage.sourceMessageId ?? processingMessage.id
                await pollForMessageCompletion(messageId: pollingMessageId)
            }
            else if let pendingMessageId = initialPendingMessageId, detail.session.isProcessing {
                await pollForMessageCompletion(messageId: pendingMessageId)
            }
            // If this is a topic-focused session (like "Dig deeper") with no messages, auto-send the topic
            else if let topic = detail.session.topic, !topic.isEmpty, detail.messages.isEmpty {
                await sendMessage(text: topic)
            }
            // If this is an article-based session with no messages, load initial suggestions
            else if detail.session.contentId != nil && detail.messages.isEmpty {
                await loadInitialSuggestions()
            }
        } catch {
            errorMessage = error.localizedDescription
            logger.error("[ViewModel] loadSession failed | error=\(error.localizedDescription)")
        }

        isLoading = false
    }

    var latestProcessSummary: String? {
        messages.last(where: \.isProcessSummary)?.processSummaryText
    }

    /// Poll for a processing message to complete
    private func pollForMessageCompletion(messageId: Int) async {
        isSending = true
        startThinkingTimer()

        do {
            // Use the polling sendMessage which handles the polling loop
            let assistantMessage = try await pollUntilComplete(messageId: messageId)
            await refreshTranscriptAfterPolling(fallbackAssistantMessage: assistantMessage)
        } catch {
            logger.error("[ViewModel] pollForMessageCompletion error | error=\(error.localizedDescription)")
            errorMessage = error.localizedDescription
        }

        isSending = false
        stopThinkingTimer()
    }

    /// Poll until message is complete
    private func pollUntilComplete(messageId: Int) async throws -> ChatMessage {
        let maxAttempts = 120 // 60 seconds at 500ms intervals
        var attempts = 0

        while attempts < maxAttempts {
            try Task.checkCancellation()

            let status = try await chatService.getMessageStatus(messageId: messageId)

            switch status.status {
            case .completed:
                guard let assistantMessage = status.assistantMessage else {
                    throw ChatServiceError.missingAssistantMessage
                }
                return assistantMessage

            case .failed:
                throw ChatServiceError.processingFailed(status.error ?? "Unknown error")

            case .processing:
                attempts += 1
                if attempts == 1 || attempts.isMultiple(of: 6) {
                    await refreshTranscriptSnapshot()
                }
                try await Task.sleep(nanoseconds: 500_000_000) // 500ms
            }
        }

        throw ChatServiceError.timeout
    }

    /// Load initial follow-up question suggestions for article-based sessions
    private func loadInitialSuggestions() async {
        isSending = true
        startThinkingTimer()

        Task {
            defer {
                isSending = false
                stopThinkingTimer()
            }
            do {
                let assistant = try await chatService.getInitialSuggestions(sessionId: sessionId)
                messages.append(assistant)
            } catch {
                logger.error("[ViewModel] loadInitialSuggestions error | error=\(error.localizedDescription)")
            }
        }
    }

    func sendMessage(text overrideText: String? = nil) async {
        let resolvedText = (overrideText ?? inputText).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !resolvedText.isEmpty, !isSending else { return }

        if overrideText == nil {
            inputText = ""
        }
        isSending = true
        errorMessage = nil
        startThinkingTimer()

        Task {
            defer {
                isSending = false
                stopThinkingTimer()
            }
            logger.info("[ViewModel] sendMessage started | sessionId=\(self.sessionId)")
            let userMessage = ChatMessage(
                id: (messages.last?.id ?? 0) + 1,
                role: .user,
                timestamp: ISO8601DateFormatter().string(from: Date()),
                content: resolvedText
            )
            messages.append(userMessage)

            do {
                let assistant = try await chatService.sendMessage(
                    sessionId: sessionId,
                    message: resolvedText
                )
                messages.append(assistant)
            } catch {
                errorMessage = error.localizedDescription
                logger.error("[ViewModel] sendMessage error | error=\(error.localizedDescription)")
            }

            isSending = false
        }
    }

    /// Request counterbalancing arguments via web search.
    func sendCounterArgumentsPrompt() async {
        let subject = counterArgumentSubject()
        let prompt = """
Find counterbalancing arguments online for \(subject). Use the exa_web_search tool to gather opposing viewpoints, cite sources with markdown links, and compare perspectives to the current article/topic.
"""
        await sendMessage(text: prompt)
    }

    private func counterArgumentSubject() -> String {
        if let topic = session?.topic, !topic.isEmpty {
            return "\"\(topic)\""
        }
        if let articleTitle = session?.articleTitle, !articleTitle.isEmpty {
            return "the article \"\(articleTitle)\""
        }
        if let title = session?.title, !title.isEmpty {
            return "\"\(title)\""
        }
        return "this topic"
    }

    /// Dig deeper into highlighted text by automatically sending a follow-up query.
    func digDeeper(into selectedText: String) async {
        let trimmed = selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let prompt = "Dig deeper into this: \"\(trimmed)\""
        await sendMessage(text: prompt)
    }

    func cancelStreaming() {
        isSending = false
        stopThinkingTimer()
    }

    /// Update the session with new data (e.g., after provider switch)
    func updateSession(_ updatedSession: ChatSessionSummary) {
        self.session = updatedSession
    }

    /// All messages including any streaming message
    var allMessages: [ChatMessage] {
        return messages
    }

    private func seedInitialPendingMessageIfNeeded() {
        guard messages.isEmpty, let initialPendingUserMessage else { return }
        messages = [initialPendingUserMessage]
    }

    private func refreshTranscriptSnapshot() async {
        do {
            let detail = try await chatService.getSession(id: sessionId)
            session = detail.session
            let filteredMessages = detail.messages.filter { !$0.content.isEmpty }
            if !filteredMessages.isEmpty {
                messages = filteredMessages
            }
        } catch {
            logger.debug("[ViewModel] refreshTranscriptSnapshot skipped | error=\(error.localizedDescription)")
        }
    }

    private func refreshTranscriptAfterPolling(fallbackAssistantMessage: ChatMessage) async {
        do {
            let detail = try await chatService.getSession(id: sessionId)
            session = detail.session
            let filteredMessages = detail.messages.filter { !$0.content.isEmpty }
            if filteredMessages.isEmpty {
                messages.append(fallbackAssistantMessage)
            } else {
                messages = filteredMessages
            }
        } catch {
            logger.debug("[ViewModel] refreshTranscriptAfterPolling fallback | error=\(error.localizedDescription)")
            if !messages.contains(where: { $0.uiIdentity == fallbackAssistantMessage.uiIdentity }) {
                messages.append(fallbackAssistantMessage)
            }
        }
    }

    // MARK: - Thinking Indicator

    private func startThinkingTimer() {
        thinkingTimer?.invalidate()
        thinkingElapsedSeconds = 0

        let timer = Timer(
            timeInterval: 1.0,
            repeats: true
        ) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                self.thinkingElapsedSeconds += 1
            }
        }
        thinkingTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func stopThinkingTimer() {
        thinkingTimer?.invalidate()
        thinkingTimer = nil
        thinkingElapsedSeconds = 0
    }

    // MARK: - Voice Dictation

    /// Check voice dictation availability and attempt token refresh if auth is stale.
    func checkAndRefreshVoiceDictation() async {
        do {
            if !hasVoiceAuthToken {
                _ = try await AuthenticationService.shared.refreshAccessToken()
            }
            voiceDictationAvailable = await OpenAIService.shared.refreshTranscriptionAvailability()
        } catch {
            logger.debug("Token refresh for voice dictation failed: \(error.localizedDescription)")
            AppSettings.shared.backendTranscriptionAvailable = false
            voiceDictationAvailable = false
        }
    }

    /// Start voice recording for chat message.
    func startVoiceRecording() async {
        if !voiceDictationAvailable {
            await checkAndRefreshVoiceDictation()
        }
        guard voiceDictationAvailable else {
            errorMessage = "Microphone is unavailable right now. Try again in a moment."
            return
        }
        configureTranscriptionCallbacks()
        activeTranscript = ""
        do {
            try await transcriptionService.start()
            isRecording = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Stop recording and transcribe into the input box.
    func stopVoiceRecording() async {
        guard isRecording else { return }
        logger.info("[ViewModel] Stopping voice recording")

        do {
            let transcription = try await transcriptionService.stop()
            let trimmedTranscription = transcription.trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            logger.info("[ViewModel] Transcription complete | length=\(trimmedTranscription.count)")
            isRecording = false
            isTranscribing = false
            guard !trimmedTranscription.isEmpty else {
                errorMessage = "I didn't catch that. Try again."
                activeTranscript = ""
                return
            }
            activeTranscript = trimmedTranscription
            await sendMessage(text: trimmedTranscription)
            activeTranscript = ""
        } catch {
            logger.error("[ViewModel] Voice transcription error: \(error.localizedDescription)")
            errorMessage = error.localizedDescription
            activeTranscript = ""
            isRecording = false
            isTranscribing = false
        }
    }

    /// Cancel voice recording.
    func cancelVoiceRecording() {
        transcriptionService.cancel()
        isRecording = false
        isTranscribing = false
        activeTranscript = ""
    }

    /// Check if voice dictation is available.
    private var isVoiceDictationAvailable: Bool {
        transcriptionService.isAvailable
    }

    private var hasVoiceAuthToken: Bool {
        if let accessToken = KeychainManager.shared.getToken(key: .accessToken), !accessToken.isEmpty {
            return true
        }
        if let refreshToken = KeychainManager.shared.getToken(key: .refreshToken), !refreshToken.isEmpty {
            return true
        }
        return false
    }

    private func configureTranscriptionCallbacks() {
        transcriptionService.onTranscriptDelta = nil
        transcriptionService.onTranscriptFinal = { [weak self] transcript in
            Task { @MainActor in
                guard let self else { return }
                self.updateTranscriptPreview(transcript)
            }
        }
        transcriptionService.onStopReason = { [weak self] reason in
            Task { @MainActor in
                guard let self else { return }
                switch reason {
                case .manual:
                    return
                case .silenceAutoStop, .cancel, .failure:
                    self.isRecording = false
                    self.isTranscribing = false
                    if reason != .silenceAutoStop {
                        self.activeTranscript = ""
                    }
                }
            }
        }
        transcriptionService.onError = { [weak self] message in
            Task { @MainActor in
                self?.errorMessage = message
                self?.isRecording = false
                self?.isTranscribing = false
                self?.activeTranscript = ""
            }
        }
        transcriptionService.onStateChange = { [weak self] state in
            Task { @MainActor in
                guard let self else { return }
                switch state {
                case .idle:
                    self.isRecording = false
                    self.isTranscribing = false
                case .recording:
                    self.isRecording = true
                    self.isTranscribing = false
                case .transcribing:
                    self.isRecording = false
                    self.isTranscribing = true
                }
            }
        }
        transcriptionService.onTranscriptDelta = { [weak self] delta in
            Task { @MainActor in
                self?.updateTranscriptPreview(delta)
            }
        }
    }

    private func updateTranscriptPreview(_ text: String) {
        let cleaned = text
            .replacingOccurrences(of: "...", with: "")
            .replacingOccurrences(of: "\u{2026}", with: "")
        let trimmed = cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        activeTranscript = trimmed
    }
}
