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
    @Published private(set) var transcriptMessages: [ChatMessage] = []
    @Published private(set) var activeTurnMessages: [ChatMessage] = []
    @Published var isLoading = false
    @Published var isSending = false
    @Published var errorMessage: String?
    @Published var inputText: String = ""
    @Published var thinkingElapsedSeconds = 0
    @Published var isStartingCouncil = false
    @Published var selectingCouncilChildSessionId: Int?

    // Voice dictation state
    @Published var isRecording = false
    @Published var isTranscribing = false
    @Published private(set) var voiceDictationAvailable = false
    @Published private(set) var isVoiceActionInFlight = false

    private let chatService = ChatService.shared
    private let transcriptionService: any SpeechTranscribing
    private var thinkingTimer: Timer?
    let sessionId: Int
    private let initialPendingUserMessage: ChatMessage?
    private let initialPendingMessageId: Int?
    private var pendingCouncilPrompt: String?
    private var hasTriggeredPendingCouncilStart = false
    private var hasAppliedVoiceTranscript = false

    init(
        sessionId: Int,
        initialPendingUserMessage: ChatMessage? = nil,
        initialPendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        initialVoiceDictationAvailable: Bool = false,
        transcriptionService: (any SpeechTranscribing)? = nil
    ) {
        self.sessionId = sessionId
        self.initialPendingUserMessage = initialPendingUserMessage
        self.initialPendingMessageId = initialPendingMessageId
        self.pendingCouncilPrompt = pendingCouncilPrompt?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.transcriptMessages = []
        self.activeTurnMessages = initialPendingUserMessage.map { [$0] } ?? []
        self.voiceDictationAvailable = initialVoiceDictationAvailable
        let resolvedService = transcriptionService ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.transcriptionService = resolvedService
    }

    init(
        session: ChatSessionSummary,
        initialPendingUserMessage: ChatMessage? = nil,
        initialPendingMessageId: Int? = nil,
        pendingCouncilPrompt: String? = nil,
        initialVoiceDictationAvailable: Bool = false,
        transcriptionService: (any SpeechTranscribing)? = nil
    ) {
        self.sessionId = session.id
        self.session = session
        self.initialPendingUserMessage = initialPendingUserMessage
        self.initialPendingMessageId = initialPendingMessageId
        self.pendingCouncilPrompt = pendingCouncilPrompt?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.transcriptMessages = []
        self.activeTurnMessages = initialPendingUserMessage.map { [$0] } ?? []
        self.voiceDictationAvailable = initialVoiceDictationAvailable
        let resolvedService = transcriptionService ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.transcriptionService = resolvedService
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
            applyDetail(detail)
            let assistantPreview = allMessages.last(where: { $0.isAssistant })?.content.prefix(160) ?? ""
            logger.debug(
                "[ViewModel] loadSession succeeded | sessionId=\(self.sessionId) messages=\(self.allMessages.count) assistantPreview=\(String(assistantPreview), privacy: .public)"
            )

            // Check if there's a processing message we need to poll for
            if let processingMessage = activeTurnMessages.first(where: { $0.isProcessing }) {
                let pollingMessageId = processingMessage.sourceMessageId ?? processingMessage.id
                await pollForMessageCompletion(messageId: pollingMessageId)
            }
            else if let pendingMessageId = initialPendingMessageId, detail.session.isProcessing {
                await pollForMessageCompletion(messageId: pendingMessageId)
            }
            else if shouldAutoStartCouncil(detail: detail) {
                hasTriggeredPendingCouncilStart = true
                let prompt = pendingCouncilPrompt ?? ""
                pendingCouncilPrompt = nil
                isLoading = false
                await startCouncil(message: prompt)
                return
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

    private func shouldAutoStartCouncil(detail: ChatSessionDetail) -> Bool {
        guard !hasTriggeredPendingCouncilStart else { return false }
        guard let prompt = pendingCouncilPrompt, !prompt.isEmpty else { return false }
        return !detail.session.isCouncilMode
    }

    var latestProcessSummary: String? {
        activeTurnMessages.last(where: \.isProcessSummary)?.processSummaryText
    }

    var allMessages: [ChatMessage] {
        transcriptMessages + activeTurnMessages
    }

    var councilCandidates: [CouncilCandidate] {
        latestCouncilMessage?.councilCandidates.sorted { $0.order < $1.order } ?? []
    }

    var activeCouncilChildSessionId: Int? {
        session?.activeChildSessionId ?? latestCouncilMessage?.activeCouncilChildSessionId
    }

    var activeCouncilCandidate: CouncilCandidate? {
        let candidates = councilCandidates
        guard !candidates.isEmpty else { return nil }
        if let activeCouncilChildSessionId,
           let candidate = candidates.first(where: { $0.childSessionId == activeCouncilChildSessionId }) {
            return candidate
        }
        return candidates.first
    }

    /// Poll for a processing message to complete
    private func pollForMessageCompletion(messageId: Int) async {
        isSending = true
        startThinkingTimer()

        do {
            // Use the polling sendMessage which handles the polling loop
            _ = try await pollUntilComplete(messageId: messageId)
            try await refreshTranscriptAfterPolling()
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
                transcriptMessages.append(assistant)
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
            do {
                let response = try await chatService.sendMessageAsync(
                    sessionId: sessionId,
                    message: resolvedText
                )
                activeTurnMessages = [response.userMessage]
                _ = try await pollUntilComplete(messageId: response.messageId)
                try await refreshTranscriptAfterPolling()
            } catch {
                activeTurnMessages = []
                errorMessage = error.localizedDescription
                logger.error("[ViewModel] sendMessage error | error=\(error.localizedDescription)")
            }
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

    var canStartCouncil: Bool {
        guard let session else { return false }
        guard !session.isCouncilMode else { return false }
        return session.sessionType != "deep_research"
    }

    private func seedInitialPendingMessageIfNeeded() {
        guard allMessages.isEmpty, let initialPendingUserMessage else { return }
        activeTurnMessages = [initialPendingUserMessage]
    }

    private func visibleMessages(from detail: ChatSessionDetail) -> [ChatMessage] {
        let filteredMessages = detail.messages.filter {
            !$0.content.isEmpty || $0.hasCouncilCandidates
        }
        return filteredMessages.isEmpty ? (initialPendingUserMessage.map { [$0] } ?? []) : filteredMessages
    }

    private func applyDetail(_ detail: ChatSessionDetail) {
        session = detail.session
        let visibleMessages = visibleMessages(from: detail)
        let pendingSourceIds = Set(
            visibleMessages.compactMap { message -> Int? in
                guard message.isProcessing else { return nil }
                return message.sourceMessageId ?? message.id
            }
        )

        if pendingSourceIds.isEmpty {
            transcriptMessages = visibleMessages
            activeTurnMessages = []
            return
        }

        transcriptMessages = visibleMessages.filter { message in
            let sourceId = message.sourceMessageId ?? message.id
            return !pendingSourceIds.contains(sourceId)
        }
        activeTurnMessages = visibleMessages.filter { message in
            let sourceId = message.sourceMessageId ?? message.id
            return pendingSourceIds.contains(sourceId)
        }
    }

    private func refreshTranscriptSnapshot() async {
        do {
            let detail = try await chatService.getSession(id: sessionId)
            applyDetail(detail)
        } catch {
            logger.debug("[ViewModel] refreshTranscriptSnapshot skipped | error=\(error.localizedDescription)")
        }
    }

    private func refreshTranscriptAfterPolling() async throws {
        let detail = try await chatService.getSession(id: sessionId)
        applyDetail(detail)
        guard !allMessages.isEmpty else {
            logger.error("[ViewModel] refreshTranscriptAfterPolling failed | no transcript messages returned")
            throw ChatServiceError.missingAssistantMessage
        }
    }

    func startCouncil(message: String) async {
        let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isStartingCouncil else { return }

        isStartingCouncil = true
        isSending = true
        errorMessage = nil
        startThinkingTimer()
        defer {
            isStartingCouncil = false
            isSending = false
            stopThinkingTimer()
        }

        do {
            let detail = try await chatService.startCouncil(
                sessionId: sessionId,
                message: trimmed
            )
            applyDetail(detail)
        } catch {
            errorMessage = error.localizedDescription
            logger.error("[ViewModel] startCouncil failed | error=\(error.localizedDescription)")
        }
    }

    func selectCouncilBranch(childSessionId: Int) async {
        guard session?.activeChildSessionId != childSessionId else { return }
        guard selectingCouncilChildSessionId == nil || selectingCouncilChildSessionId == childSessionId else { return }

        selectingCouncilChildSessionId = childSessionId
        errorMessage = nil
        defer { selectingCouncilChildSessionId = nil }

        do {
            let detail = try await chatService.selectCouncilBranch(
                sessionId: sessionId,
                childSessionId: childSessionId
            )
            applyDetail(detail)
        } catch {
            errorMessage = error.localizedDescription
            logger.error("[ViewModel] selectCouncilBranch failed | error=\(error.localizedDescription)")
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

    private var latestCouncilMessage: ChatMessage? {
        allMessages.last(where: \.hasCouncilCandidates)
    }

    // MARK: - Voice Dictation

    /// Check voice dictation availability and attempt token refresh if auth is stale.
    func checkAndRefreshVoiceDictation() async {
        if transcriptionService.isAvailable {
            voiceDictationAvailable = true
            return
        }

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
        guard !isRecording, !isTranscribing else { return }
        hasAppliedVoiceTranscript = false
        if !voiceDictationAvailable {
            await checkAndRefreshVoiceDictation()
        }
        guard voiceDictationAvailable else {
            errorMessage = "Microphone is unavailable right now. Try again in a moment."
            return
        }
        errorMessage = nil
        configureTranscriptionCallbacks()
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
            let trimmedTranscription = try await transcriptionService.stop().trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            logger.info("[ViewModel] Transcription complete | length=\(trimmedTranscription.count)")
            isRecording = false
            isTranscribing = false
            applyVoiceTranscript(trimmedTranscription)
        } catch {
            logger.error("[ViewModel] Voice transcription error: \(error.localizedDescription)")
            errorMessage = error.localizedDescription
            isRecording = false
            isTranscribing = false
        }
    }

    func toggleVoiceRecording() async {
        guard !isVoiceActionInFlight, !isTranscribing else { return }

        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        if isRecording {
            await stopVoiceRecording()
        } else {
            await startVoiceRecording()
        }
    }

    /// Cancel voice recording.
    func cancelVoiceRecording() {
        transcriptionService.cancel()
        isRecording = false
        isTranscribing = false
        isVoiceActionInFlight = false
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
            self?.applyVoiceTranscript(transcript)
        }
        transcriptionService.onStopReason = { [weak self] reason in
            guard let self else { return }
            switch reason {
            case .manual:
                return
            case .silenceAutoStop, .cancel, .failure:
                self.isRecording = false
                self.isTranscribing = false
            }
        }
        transcriptionService.onError = { [weak self] message in
            self?.errorMessage = message
            self?.isRecording = false
            self?.isTranscribing = false
        }
        transcriptionService.onStateChange = { [weak self] state in
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

    private func applyVoiceTranscript(_ transcript: String) {
        let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTranscript.isEmpty else {
            errorMessage = "I didn't catch that. Try again."
            return
        }
        guard !hasAppliedVoiceTranscript else { return }

        hasAppliedVoiceTranscript = true
        errorMessage = nil
        let existingInput = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        if existingInput.isEmpty {
            inputText = trimmedTranscript
        } else {
            inputText = "\(existingInput) \(trimmedTranscript)"
        }
    }
}
