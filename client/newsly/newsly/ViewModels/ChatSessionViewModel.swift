//
//  ChatSessionViewModel.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation
import Observation
import SwiftUI
import os

private let logger = Logger(subsystem: "com.newsly", category: "ChatSessionViewModel")
private let chatPerfSignposter = OSSignposter(subsystem: "com.newsly.chat", category: "perf")

/// Owns the visible chat transcript, local pending sends, polling, council selection, and voice state.
///
/// Streaming readiness: the timeline reconciler is intentionally isolated so a future SSE
/// implementation can add an `apply(streamChunk:)` path that updates the active assistant row
/// before the final status/detail reconcile, without rewriting row identity or scroll ownership.
@MainActor
@Observable
final class ChatSessionViewModel {
    var session: ChatSessionSummary?
    private(set) var timeline: [ChatTimelineItem] = []
    var isLoading = false
    var isSending = false
    var errorMessage: String?
    var inputText: String = ""
    var thinkingElapsedSeconds = 0
    var isStartingCouncil = false
    var selectingCouncilChildSessionId: Int?
    var retryingCouncilChildSessionId: Int?
    private(set) var councilSelectionTimedOut = false

    // Voice dictation state
    var isRecording = false
    var isTranscribing = false
    private(set) var voiceDictationAvailable = false
    private(set) var isVoiceActionInFlight = false

    private let chatService: any ChatSessionServicing
    private let transcriptionService: any SpeechTranscribing
    private let activeSessionManager: ActiveChatSessionManager
    private let timelineReconciler = ChatTimelineReconciler()
    @ObservationIgnored
    private var thinkingTimer: Timer?
    let sessionId: Int
    private let initialPendingMessageId: Int?
    @ObservationIgnored
    private var pendingCouncilPrompt: String?
    @ObservationIgnored
    private var hasTriggeredPendingCouncilStart = false
    @ObservationIgnored
    private var hasAppliedVoiceTranscript = false
    @ObservationIgnored
    private var pendingSends: [UUID: PendingSend] = [:]
    @ObservationIgnored
    private var localIdentityAliases: [ChatTimelineID: UUID] = [:]
    @ObservationIgnored
    private var selectCouncilTask: Task<Void, Never>?
    @ObservationIgnored
    private var selectCouncilDeadlineTask: Task<Void, Never>?
    @ObservationIgnored
    private var selectCouncilRequestId: UUID?
    @ObservationIgnored
    private var sendTask: Task<Void, Never>?
    @ObservationIgnored
    private var startCouncilTask: Task<Void, Never>?
    @ObservationIgnored
    private var retryCouncilTask: Task<Void, Never>?
    @ObservationIgnored
    private var digDeeperTask: Task<Void, Never>?
    @ObservationIgnored
    private var voiceActionTask: Task<Void, Never>?

    init(
        route: ChatSessionRoute,
        dependencies: ChatDependencies,
        initialVoiceDictationAvailable: Bool = false,
    ) {
        let initialPendingUserMessage = Self.initialPendingUserMessage(from: route)
        let initialPendingLocalId = initialPendingUserMessage.map { _ in UUID() }
        self.chatService = dependencies.chatService
        self.transcriptionService = dependencies.transcriptionService
        self.activeSessionManager = dependencies.activeSessionManager
        self.sessionId = route.sessionId
        self.session = route.session
        self.initialPendingMessageId = route.pendingMessageId
        self.pendingCouncilPrompt = route.pendingCouncilPrompt?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.voiceDictationAvailable = initialVoiceDictationAvailable
        configureInitialPendingMessage(initialPendingUserMessage, localId: initialPendingLocalId)
    }

    deinit {
        thinkingTimer?.invalidate()
        sendTask?.cancel()
        startCouncilTask?.cancel()
        retryCouncilTask?.cancel()
        digDeeperTask?.cancel()
        voiceActionTask?.cancel()
        selectCouncilTask?.cancel()
        selectCouncilDeadlineTask?.cancel()
    }

    func performSendMessage(text overrideText: String? = nil) {
        sendTask?.cancel()
        sendTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.sendMessage(text: overrideText)
            self.sendTask = nil
        }
    }

    func performStartCouncil(message: String) {
        startCouncilTask?.cancel()
        startCouncilTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.startCouncil(message: message)
            self.startCouncilTask = nil
        }
    }

    func performRetryCouncilCandidate(childSessionId: Int) {
        retryCouncilTask?.cancel()
        retryCouncilTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.retryCouncilCandidate(childSessionId: childSessionId)
            self.retryCouncilTask = nil
        }
    }

    func performDigDeeper(into selectedText: String) {
        digDeeperTask?.cancel()
        digDeeperTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.digDeeper(into: selectedText)
            self.digDeeperTask = nil
        }
    }

    func performToggleVoiceRecording() {
        voiceActionTask?.cancel()
        voiceActionTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.toggleVoiceRecording()
            self.voiceActionTask = nil
        }
    }

    func loadSession() async {
        let signpostState = chatPerfSignposter.beginInterval("load-session")
        defer { chatPerfSignposter.endInterval("load-session", signpostState) }

        logger.debug("[ViewModel] loadSession | sessionId=\(self.sessionId)")
        isLoading = true
        errorMessage = nil

        do {
            let detail = try await chatService.getSession(id: sessionId)
            applyDetail(detail)
            let assistantPreview = allMessages.last(where: { $0.isAssistant })?.content.prefix(160) ?? ""
            logger.debug(
                "[ViewModel] loadSession succeeded | sessionId=\(self.sessionId) messages=\(self.allMessages.count) assistantPreview=\(String(assistantPreview), privacy: .public)"
            )

            // Check if there's a processing message we need to poll for
            if let processingMessage = allMessages.first(where: { $0.isProcessing }) {
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
        } catch is CancellationError {
            logger.debug("[ViewModel] loadSession cancelled | sessionId=\(self.sessionId)")
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
        allMessages.last(where: \.isProcessSummary)?.processSummaryText
    }

    var allMessages: [ChatMessage] {
        timeline.map(\.message)
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
        } catch is CancellationError {
            logger.debug("[ViewModel] pollForMessageCompletion cancelled | sessionId=\(self.sessionId)")
        } catch {
            logger.error("[ViewModel] pollForMessageCompletion error | error=\(error.localizedDescription)")
            errorMessage = error.localizedDescription
        }

        isSending = false
        stopThinkingTimer()
    }

    /// Poll until message is complete
    private func pollUntilComplete(messageId: Int) async throws -> ChatMessage {
        let signpostState = chatPerfSignposter.beginInterval("poll-cycle")
        defer { chatPerfSignposter.endInterval("poll-cycle", signpostState) }

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
                upsertServerMessage(assistantMessage)
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
        defer {
            isSending = false
            stopThinkingTimer()
        }

        do {
            let assistant = try await chatService.getInitialSuggestions(sessionId: sessionId)
            upsertServerMessage(assistant)
        } catch is CancellationError {
            logger.debug("[ViewModel] loadInitialSuggestions cancelled | sessionId=\(self.sessionId)")
        } catch {
            logger.error("[ViewModel] loadInitialSuggestions error | error=\(error.localizedDescription)")
        }
    }

    func sendMessage(text overrideText: String? = nil) async {
        let resolvedText = (overrideText ?? inputText).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !resolvedText.isEmpty, !isSending else { return }

        let signpostState = chatPerfSignposter.beginInterval("send-message")
        defer { chatPerfSignposter.endInterval("send-message", signpostState) }

        if overrideText == nil {
            inputText = ""
        }
        isSending = true
        errorMessage = nil
        startThinkingTimer()
        let localId = UUID()
        let pending = PendingSend(
            localId: localId,
            text: resolvedText,
            messageId: nil,
            createdAt: ISO8601DateFormatter().string(from: Date())
        )
        pendingSends[localId] = pending
        upsertPendingSend(pending)

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
            var acknowledgedPending = pending
            acknowledgedPending.messageId = response.messageId
            pendingSends[localId] = acknowledgedPending
            upsertSentUserMessage(response.userMessage, localId: localId, messageId: response.messageId)
            _ = try await pollUntilComplete(messageId: response.messageId)
            try await refreshTranscriptAfterPolling()
            pendingSends.removeValue(forKey: localId)
        } catch is CancellationError {
            pendingSends.removeValue(forKey: localId)
            timeline.removeAll { $0.id == .local(localId) }
            logger.debug("[ViewModel] sendMessage cancelled | sessionId=\(self.sessionId)")
        } catch {
            errorMessage = error.localizedDescription
            markPendingSendFailed(localId: localId, error: error.localizedDescription)
            logger.error("[ViewModel] sendMessage error | error=\(error.localizedDescription)")
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

    func handleDisappear() {
        handOffBackgroundPollingIfNeeded()
        sendTask?.cancel()
        sendTask = nil
        startCouncilTask?.cancel()
        startCouncilTask = nil
        retryCouncilTask?.cancel()
        retryCouncilTask = nil
        digDeeperTask?.cancel()
        digDeeperTask = nil
        voiceActionTask?.cancel()
        voiceActionTask = nil
        selectCouncilTask?.cancel()
        selectCouncilTask = nil
        selectCouncilDeadlineTask?.cancel()
        selectCouncilDeadlineTask = nil
        selectCouncilRequestId = nil
        selectingCouncilChildSessionId = nil
        retryingCouncilChildSessionId = nil
        councilSelectionTimedOut = false
        isLoading = false
        isSending = false
        isStartingCouncil = false
        stopThinkingTimer()
        transcriptionService.reset()
        isRecording = false
        isTranscribing = false
        isVoiceActionInFlight = false
    }

    private func handOffBackgroundPollingIfNeeded() {
        guard let session else { return }
        guard let contentId = session.contentId else { return }
        guard let processingMessageId = backgroundTrackingMessageId else { return }

        activeSessionManager.startTracking(
            session: session,
            contentId: contentId,
            contentTitle: session.articleTitle ?? session.displayTitle,
            messageId: processingMessageId
        )
    }

    private var backgroundTrackingMessageId: Int? {
        if let processingMessage = allMessages.first(where: { $0.isProcessing }) {
            return processingMessage.sourceMessageId ?? processingMessage.id
        }

        if session?.isProcessing == true {
            return initialPendingMessageId
        }

        return nil
    }

    private static func initialPendingUserMessage(from route: ChatSessionRoute) -> ChatMessage? {
        guard
            let text = route.initialUserMessageText,
            !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            return nil
        }

        return ChatMessage(
            id: route.pendingMessageId ?? route.sessionId,
            sourceMessageId: route.pendingMessageId,
            role: .user,
            timestamp: route.initialUserMessageTimestamp ?? ISO8601DateFormatter().string(from: Date()),
            content: text,
            status: .processing
        )
    }

    /// Update the session with new data (e.g., after provider switch)
    func updateSession(_ updatedSession: ChatSessionSummary) {
        self.session = updatedSession
    }

    var canStartCouncil: Bool {
        session?.canStartCouncil ?? false
    }

    private func applyDetail(_ detail: ChatSessionDetail) {
        let signpostState = chatPerfSignposter.beginInterval("reconcile-detail")
        defer { chatPerfSignposter.endInterval("reconcile-detail", signpostState) }

        session = detail.session
        publishTimeline(
            timelineReconciler.reconcile(
                current: timeline,
                detail: detail,
                pendingSends: pendingSends,
                localIdentityAliases: &localIdentityAliases
            )
        )
    }

    private func configureInitialPendingMessage(_ message: ChatMessage?, localId: UUID?) {
        guard let message, let localId else { return }

        if let initialPendingMessageId {
            pendingSends[localId] = PendingSend(
                localId: localId,
                text: message.content,
                messageId: initialPendingMessageId,
                createdAt: message.timestamp
            )
        }
        localIdentityAliases[ChatTimelineID.server(for: message)] = localId
        publishTimeline([
            ChatTimelineItem(
                id: .local(localId),
                message: message,
                pendingMessageId: initialPendingMessageId,
                retryText: nil
            )
        ])
    }

    private func publishTimeline(_ items: [ChatTimelineItem]) {
        timeline = items.sorted { lhs, rhs in
            let lhsKey = (lhs.message.timestamp, lhs.message.displayType.sortOrder, lhs.id.sortKey)
            let rhsKey = (rhs.message.timestamp, rhs.message.displayType.sortOrder, rhs.id.sortKey)
            return lhsKey < rhsKey
        }
    }

    private func upsertPendingSend(_ pending: PendingSend) {
        upsertTimelineItem(
            ChatTimelineItem(
                id: .local(pending.localId),
                message: pending.placeholderMessage,
                pendingMessageId: pending.messageId,
                retryText: pending.text
            )
        )
    }

    private func upsertSentUserMessage(_ message: ChatMessage, localId: UUID, messageId: Int) {
        localIdentityAliases[ChatTimelineID.server(for: message)] = localId
        upsertTimelineItem(
            ChatTimelineItem(
                id: .local(localId),
                message: message,
                pendingMessageId: messageId,
                retryText: nil
            )
        )
    }

    private func upsertServerMessage(_ message: ChatMessage) {
        upsertTimelineItem(
            ChatTimelineItem(
                id: ChatTimelineID.server(for: message),
                message: message,
                pendingMessageId: message.isProcessing ? message.sourceMessageId ?? message.id : nil,
                retryText: nil
            )
        )
    }

    private func upsertTimelineItem(_ item: ChatTimelineItem) {
        var itemsById = Dictionary(uniqueKeysWithValues: timeline.map { ($0.id, $0) })
        itemsById[item.id] = item
        publishTimeline(Array(itemsById.values))
    }

    private func markPendingSendFailed(localId: UUID, error: String) {
        pendingSends.removeValue(forKey: localId)
        guard let existing = timeline.first(where: { $0.id == .local(localId) }) else {
            return
        }
        upsertTimelineItem(
            ChatTimelineItem(
                id: existing.id,
                message: ChatMessage(
                    id: existing.message.id,
                    sourceMessageId: existing.message.sourceMessageId,
                    role: existing.message.role,
                    timestamp: existing.message.timestamp,
                    content: existing.message.content,
                    displayType: existing.message.displayType,
                    processLabel: existing.message.processLabel,
                    status: .failed,
                    error: error,
                    feedOptions: existing.message.feedOptions,
                    councilCandidates: existing.message.councilCandidates,
                    activeCouncilChildSessionId: existing.message.activeCouncilChildSessionId
                ),
                pendingMessageId: existing.pendingMessageId,
                retryText: existing.retryText ?? existing.message.content
            )
        )
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

        let signpostState = chatPerfSignposter.beginInterval("start-council")
        defer { chatPerfSignposter.endInterval("start-council", signpostState) }

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
        } catch is CancellationError {
            logger.debug("[ViewModel] startCouncil cancelled | sessionId=\(self.sessionId)")
        } catch {
            errorMessage = error.localizedDescription
            logger.error("[ViewModel] startCouncil failed | error=\(error.localizedDescription)")
        }
    }

    func selectCouncilBranch(childSessionId: Int) async {
        guard session?.activeChildSessionId != childSessionId else { return }
        selectCouncilTask?.cancel()
        selectCouncilDeadlineTask?.cancel()
        let requestId = UUID()
        selectCouncilRequestId = requestId
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            let signpostState = chatPerfSignposter.beginInterval("select-council-branch")
            defer { chatPerfSignposter.endInterval("select-council-branch", signpostState) }

            self.selectingCouncilChildSessionId = childSessionId
            self.councilSelectionTimedOut = false
            self.errorMessage = nil
            self.startCouncilSelectionDeadline(requestId: requestId)
            defer {
                if self.selectCouncilRequestId == requestId {
                    self.selectingCouncilChildSessionId = nil
                    self.selectCouncilTask = nil
                    self.selectCouncilDeadlineTask?.cancel()
                    self.selectCouncilDeadlineTask = nil
                    self.selectCouncilRequestId = nil
                    self.councilSelectionTimedOut = false
                }
            }

            do {
                let detail = try await self.chatService.selectCouncilBranch(
                    sessionId: self.sessionId,
                    childSessionId: childSessionId
                )
                try Task.checkCancellation()
                self.applyDetail(detail)
                self.errorMessage = nil
            } catch is CancellationError {
                logger.debug("[ViewModel] selectCouncilBranch cancelled")
            } catch {
                self.errorMessage = error.localizedDescription
                logger.error("[ViewModel] selectCouncilBranch failed | error=\(error.localizedDescription)")
            }
        }
        selectCouncilTask = task
        await task.value
    }

    func cancelCouncilSelection() {
        selectCouncilTask?.cancel()
        selectCouncilTask = nil
        selectCouncilDeadlineTask?.cancel()
        selectCouncilDeadlineTask = nil
        selectCouncilRequestId = nil
        selectingCouncilChildSessionId = nil
        councilSelectionTimedOut = false
        errorMessage = nil
    }

    func retryCouncilCandidate(childSessionId: Int) async {
        guard retryingCouncilChildSessionId == nil else { return }

        retryingCouncilChildSessionId = childSessionId
        errorMessage = nil
        defer {
            retryingCouncilChildSessionId = nil
        }

        do {
            let detail = try await chatService.retryCouncilBranch(
                sessionId: sessionId,
                childSessionId: childSessionId
            )
            applyDetail(detail)
        } catch is CancellationError {
            logger.debug("[ViewModel] retryCouncilCandidate cancelled | sessionId=\(self.sessionId)")
        } catch {
            errorMessage = error.localizedDescription
            logger.error("[ViewModel] retryCouncilCandidate failed | error=\(error.localizedDescription)")
        }
    }

    private func startCouncilSelectionDeadline(requestId: UUID) {
        selectCouncilDeadlineTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(nanoseconds: 10_000_000_000)
            } catch {
                return
            }

            guard let self, self.selectCouncilRequestId == requestId else { return }
            self.councilSelectionTimedOut = true
            self.errorMessage = "Switching perspectives is taking longer than expected."
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
        let signpostState = chatPerfSignposter.beginInterval("apply-voice-transcript")
        defer { chatPerfSignposter.endInterval("apply-voice-transcript", signpostState) }

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
