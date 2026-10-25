//
//  ChatSessionViewModel.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import Foundation
import Observation
import os

private let logger = Logger(subsystem: "com.newsly", category: "ChatSessionViewModel")
private let chatPerfSignposter = OSSignposter(subsystem: "com.newsly.chat", category: "perf")

private enum ChatSessionTaskKey: Hashable {
    case send
    case digDeeper
    case voiceAction
}

/// Owns the visible chat transcript, local pending sends, and polling.
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
    private(set) var loadErrorMessage: String?
    var errorMessage: String?
    var inputText: String = ""
    var thinkingStartedAt: Date?

    var isRecording: Bool { voiceInput.isRecording }
    var isTranscribing: Bool { voiceInput.isTranscribing }
    var voiceDictationAvailable: Bool { voiceInput.isAvailable }
    var isVoiceActionInFlight: Bool { voiceInput.isActionInFlight }

    private let chatService: any ChatSessionServicing
    private let messageCompletionRegistry: ChatMessageCompletionRegistry
    private let voiceInput: ChatVoiceInputController
    private let activeSessionManager: ActiveChatSessionManager
    private let lifecycle: AppLifecycle
    private let timelineReconciler = ChatTimelineReconciler()
    let sessionId: Int
    private let initialPendingMessageId: Int?
    @ObservationIgnored
    private var pendingSends: [UUID: PendingSend] = [:]
    @ObservationIgnored
    private var sendQueue = PendingSendQueue()
    @ObservationIgnored
    private var localIdentityAliases: [ChatTimelineID: UUID] = [:]
    @ObservationIgnored
    private let tasks = TaskBag<ChatSessionTaskKey>()
    @ObservationIgnored
    private var isRouteVisible = true
    @ObservationIgnored
    private var isViewActive: Bool
    @ObservationIgnored
    private var needsForegroundTranscriptRefresh = false

    init(
        lifecycle: AppLifecycle,
        route: ChatSessionRoute,
        dependencies: ChatDependencies,
        initialVoiceDictationAvailable: Bool = false,
    ) {
        let initialPendingUserMessage = Self.initialPendingUserMessage(from: route)
        let initialPendingLocalId = initialPendingUserMessage.map { _ in UUID() }
        self.chatService = dependencies.chatService
        self.messageCompletionRegistry = dependencies.messageCompletionRegistry
        self.voiceInput = ChatVoiceInputController(
            transcriptionService: dependencies.transcriptionService,
            refreshAvailability: dependencies.refreshTranscriptionAvailability,
            initiallyAvailable: initialVoiceDictationAvailable
        )
        self.activeSessionManager = dependencies.activeSessionManager
        self.lifecycle = lifecycle
        self.isViewActive = lifecycle.phase == .active
        self.sessionId = route.sessionId
        self.session = route.session
        self.initialPendingMessageId = route.pendingMessageId
        configureInitialPendingMessage(initialPendingUserMessage, localId: initialPendingLocalId)
        voiceInput.configure(
            onTranscriptReady: { [weak self] transcript in
                await self?.sendVoiceTranscript(transcript)
            },
            onError: { [weak self] message in
                self?.errorMessage = message
            }
        )
    }

    deinit {
        tasks.cancelAll()
    }

    func handleAppear() {
        isRouteVisible = true
        resumeVisibleRouteWorkIfPossible()
    }

    func performSendMessage(text overrideText: String? = nil) {
        _ = startSendMessage(text: overrideText)
    }

    private func startSendMessage(text overrideText: String?) -> Task<Void, Never>? {
        guard let pending = stagePendingSend(text: overrideText) else { return nil }
        if isSending || tasks.isRunning(.send) {
            enqueuePendingSend(pending)
            return nil
        }
        return tasks.runReplacing(.send) { [weak self] in
            guard let self else { return }
            await self.processPendingSend(pending)
            await self.drainQueuedSends()
        }
    }

    func performDigDeeper(into selectedText: String) {
        tasks.runReplacing(.digDeeper) { [weak self] in
            guard let self else { return }
            await self.digDeeper(into: selectedText)
        }
    }

    func performToggleVoiceRecording() {
        tasks.runReplacing(.voiceAction) { [weak self] in
            guard let self else { return }
            await self.toggleVoiceRecording()
        }
    }

    func loadSession() async {
        let signpostState = chatPerfSignposter.beginInterval("load-session")
        defer { chatPerfSignposter.endInterval("load-session", signpostState) }

        logger.debug("[ViewModel] loadSession | sessionId=\(self.sessionId)")
        isLoading = true
        loadErrorMessage = nil
        errorMessage = nil

        do {
            let detail = try await chatService.getSession(id: sessionId)
            applyDetail(detail)
            let assistantPreview = timeline.last(where: { $0.message.isAssistant })?.message.content.prefix(160) ?? ""
            logger.debug(
                "[ViewModel] loadSession succeeded | sessionId=\(self.sessionId) messages=\(self.timeline.count) assistantPreview=\(String(assistantPreview), privacy: .public)"
            )

            // Check if there's a processing message we need to poll for
            if let pollingMessageId = timeline.lazy.compactMap({ item -> Int? in
                guard item.message.isProcessing else { return nil }
                return item.message.sourceMessageId ?? item.pendingMessageId
            }).first {
                await pollForMessageCompletion(messageId: pollingMessageId)
            }
            else if let pendingMessageId = initialPendingMessageId, detail.session.isProcessing {
                await pollForMessageCompletion(messageId: pendingMessageId)
            }
            // If this is a topic-focused session (like "Dig deeper") with no messages, auto-send the topic
            else if let topic = detail.session.topic, !topic.isEmpty, detail.messages.isEmpty {
                await sendMessage(text: topic)
            }
        } catch where isCancelledOperation(error) {
            logger.debug("[ViewModel] loadSession cancelled | sessionId=\(self.sessionId)")
        } catch {
            loadErrorMessage = error.localizedDescription
            logger.error("[ViewModel] loadSession failed | error=\(error.localizedDescription)")
        }

        isLoading = false
        if !isSending {
            await drainQueuedSends()
        }
    }

    /// Cached derived state. Updated in `publishTimeline` so body-path reads are O(1)
    /// instead of re-scanning and re-sorting the timeline on every observation tick.
    private(set) var latestProcessSummary: String?
    private(set) var hasVisiblePartialResponse = false

    /// Poll for a processing message to complete
    private func pollForMessageCompletion(messageId: Int) async {
        isSending = true
        startThinkingTimer()

        do {
            _ = try await waitForMessageCompletion(messageId: messageId)
            try await refreshTranscriptAfterPolling()
        } catch where isCancelledOperation(error) {
            logger.debug("[ViewModel] pollForMessageCompletion cancelled | sessionId=\(self.sessionId)")
        } catch {
            removePartialAssistantMessage(messageId: messageId)
            logger.error("[ViewModel] pollForMessageCompletion error | error=\(error.localizedDescription)")
            errorMessage = error.localizedDescription
        }

        isSending = false
        stopThinkingTimer()
    }

    private func waitForMessageCompletion(messageId: Int) async throws -> ChatMessage {
        let signpostState = chatPerfSignposter.beginInterval("poll-cycle")
        defer { chatPerfSignposter.endInterval("poll-cycle", signpostState) }

        let assistantMessage = try await messageCompletionRegistry.waitForCompletion(
            messageId: messageId,
            onProcessing: { [weak self] attempt in
                guard
                    let self,
                    self.isViewActive,
                    attempt == 1 || attempt.isMultiple(of: 6)
                else {
                    return
                }
                await self.refreshTranscriptSnapshot()
            },
            onPartial: { [weak self] update in
                self?.applyPartialResponse(update, messageId: messageId)
            }
        )
        upsertServerMessage(assistantMessage)
        return assistantMessage
    }

    func sendMessage(text overrideText: String? = nil) async {
        await startSendMessage(text: overrideText)?.value
    }

    private func stagePendingSend(text overrideText: String?) -> PendingSend? {
        let resolvedText = (overrideText ?? inputText).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !resolvedText.isEmpty else { return nil }

        if overrideText == nil {
            inputText = ""
        }
        let localId = UUID()
        let pending = PendingSend(
            localId: localId,
            text: resolvedText,
            messageId: nil,
            createdAt: Date()
        )
        pendingSends[localId] = pending
        upsertPendingSend(pending)
        return pending
    }

    private func enqueuePendingSend(_ pending: PendingSend) {
        sendQueue.enqueue(pending)
        upsertPendingSend(pending)
        logger.info(
            "[ViewModel] message queued | sessionId=\(self.sessionId) queuedCount=\(self.sendQueue.count)"
        )
    }

    private func drainQueuedSends() async {
        while isViewActive, !Task.isCancelled, let queued = sendQueue.dequeue() {
            let localId = queued.localId
            guard let pending = pendingSends[localId], pending.messageId == nil else {
                continue
            }
            await processPendingSend(pending)
        }
    }

    private func startQueuedSendDrainIfPossible() {
        guard
            isViewActive,
            !needsForegroundTranscriptRefresh,
            !sendQueue.isEmpty,
            !tasks.isRunning(.send)
        else {
            return
        }
        tasks.runReplacing(.send) { [weak self] in
            await self?.drainQueuedSends()
        }
    }

    private func processPendingSend(_ pending: PendingSend) async {
        let signpostState = chatPerfSignposter.beginInterval("send-message")
        defer { chatPerfSignposter.endInterval("send-message", signpostState) }

        let localId = pending.localId
        let resolvedText = pending.text
        upsertPendingSend(pending)
        isSending = true
        errorMessage = nil
        startThinkingTimer()

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
            if suspendAcceptedSendIfInactive(localId: localId, messageId: response.messageId) {
                return
            }
            _ = try await waitForMessageCompletion(messageId: response.messageId)
            try await refreshTranscriptAfterPolling()
            pendingSends.removeValue(forKey: localId)
        } catch where isCancelledOperation(error) {
            if pendingSends[localId]?.messageId != nil {
                pendingSends.removeValue(forKey: localId)
                needsForegroundTranscriptRefresh = true
                logger.debug("[ViewModel] sendMessage polling cancelled after server acknowledgement | sessionId=\(self.sessionId)")
            } else {
                discardPendingSend(localId: localId)
                logger.debug("[ViewModel] sendMessage cancelled before server acknowledgement | sessionId=\(self.sessionId)")
            }
        } catch {
            if let messageId = pendingSends[localId]?.messageId {
                removePartialAssistantMessage(messageId: messageId)
            }
            errorMessage = error.localizedDescription
            markPendingSendFailed(localId: localId, error: error.localizedDescription)
            logger.error("[ViewModel] sendMessage error | error=\(error.localizedDescription)")
        }
    }

    /// Dig deeper into highlighted text by automatically sending a follow-up query.
    func digDeeper(into selectedText: String) async {
        let trimmed = selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        let prompt = "Dig deeper into this: \"\(trimmed)\""
        await sendMessage(text: prompt)
    }

    func handleDisappear() {
        isRouteVisible = false
        prepareForInactiveView()
        tasks.cancel(.digDeeper)
        tasks.cancel(.voiceAction)
        isLoading = false
        if !tasks.isRunning(.send) {
            isSending = false
            stopThinkingTimer()
        }
        voiceInput.reset()
    }

    func handleLifecyclePhaseChange() {
        switch lifecycle.phase {
        case .active:
            resumeVisibleRouteWorkIfPossible()
        case .inactive:
            break
        case .background:
            guard isRouteVisible else { return }
            prepareForInactiveView()
        }
    }

    func resumeAfterActivationIfNeeded() async {
        guard isRouteVisible, lifecycle.phase == .active else { return }
        resumeVisibleRouteWorkIfPossible()
        await refreshAfterForegroundIfNeeded()
    }

    private func resumeVisibleRouteWorkIfPossible() {
        guard isRouteVisible, lifecycle.phase == .active else { return }
        isViewActive = true
        startQueuedSendDrainIfPossible()
    }

    private func refreshAfterForegroundIfNeeded() async {
        guard needsForegroundTranscriptRefresh else { return }
        needsForegroundTranscriptRefresh = false
        activeSessionManager.stopTracking(sessionId: sessionId)
        await loadSession()
    }

    private func prepareForInactiveView() {
        isViewActive = false

        if let messageId = backgroundTrackingMessageId {
            handOffBackgroundPolling(messageId: messageId)
            tasks.cancel(.send)
            needsForegroundTranscriptRefresh = true
        }
    }

    private func handOffBackgroundPolling(messageId processingMessageId: Int) {
        guard let session else { return }
        guard session.contentId != nil || session.newsItemId != nil else { return }

        activeSessionManager.startTracking(
            session: session,
            contentId: session.contentId,
            newsItemId: session.newsItemId,
            contentTitle: session.articleTitle ?? session.displayTitle,
            messageId: processingMessageId
        )
    }

    private var backgroundTrackingMessageId: Int? {
        if let processingItem = timeline.first(where: { $0.message.isProcessing }) {
            return processingItem.message.sourceMessageId ?? processingItem.pendingMessageId
        }

        if session?.isProcessing == true {
            return initialPendingMessageId
        }

        return nil
    }

    private func suspendForegroundPollingIfInactive(messageId: Int) -> Bool {
        guard !isViewActive else { return false }
        needsForegroundTranscriptRefresh = true
        handOffBackgroundPolling(messageId: messageId)
        logger.debug(
            "[ViewModel] foreground polling suspended while inactive | sessionId=\(self.sessionId) messageId=\(messageId)"
        )
        return true
    }

    private func suspendAcceptedSendIfInactive(localId: UUID, messageId: Int) -> Bool {
        guard !isViewActive else { return false }

        pendingSends.removeValue(forKey: localId)
        _ = suspendForegroundPollingIfInactive(messageId: messageId)
        logger.debug(
            "[ViewModel] sendMessage accepted while inactive; foreground polling suspended | sessionId=\(self.sessionId) messageId=\(messageId)"
        )
        return true
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
            timestamp: route.initialUserMessageTimestamp ?? Date(),
            content: text,
            status: .processing
        )
    }

    /// Update the session with new data (e.g., after provider switch)
    func updateSession(_ updatedSession: ChatSessionSummary) {
        self.session = updatedSession
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
        timeline = items.map { item in
            var resolved = item
            if case .local(let localId) = item.id {
                resolved.isQueued = sendQueue.contains(localId: localId)
            }
            return resolved
        }
        .sorted { $0.isOrderedBefore($1) }
        refreshDerivedTimelineState()
    }

    private func refreshDerivedTimelineState() {
        var latestProcessSummaryValue: String?
        for item in timeline.reversed() {
            if latestProcessSummaryValue == nil, item.message.isProcessSummary {
                latestProcessSummaryValue = item.message.processSummaryText
            }
            if latestProcessSummaryValue != nil {
                break
            }
        }
        latestProcessSummary = latestProcessSummaryValue
        hasVisiblePartialResponse = timeline.contains { $0.message.isVisiblePartialResponse }
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

    private func applyPartialResponse(
        _ update: ChatPartialResponseUpdate,
        messageId: Int
    ) {
        if let message = update.message {
            upsertServerMessage(message)
            return
        }
        removePartialAssistantMessage(messageId: messageId)
    }

    private func removePartialAssistantMessage(messageId: Int) {
        let partialId = ChatTimelineID.server(
            sourceMessageId: messageId,
            role: .assistant,
            displayType: .message
        )
        publishTimeline(
            timeline.filter { item in
                item.id != partialId || !item.message.isProcessing
            }
        )
    }

    private func upsertTimelineItem(_ item: ChatTimelineItem) {
        if let existingIndex = timeline.firstIndex(where: { $0.id == item.id }) {
            var replacement = item
            if case .local(let localId) = item.id {
                replacement.isQueued = sendQueue.contains(localId: localId)
            }
            var updated = timeline
            updated[existingIndex] = replacement
            timeline = updated
            refreshDerivedTimelineState()
            return
        }
        publishTimeline(timeline + [item])
    }

    private func discardPendingSend(localId: UUID) {
        pendingSends.removeValue(forKey: localId)
        publishTimeline(timeline.filter { $0.id != .local(localId) })
    }

    private func markPendingSendFailed(localId: UUID, error: String) {
        pendingSends.removeValue(forKey: localId)
        guard let existing = timeline.first(where: { $0.id == .local(localId) }) else {
            return
        }
        upsertTimelineItem(
            ChatTimelineItem(
                id: existing.id,
                message: existing.message.failed(error: error),
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
        guard !timeline.isEmpty else {
            logger.error("[ViewModel] refreshTranscriptAfterPolling failed | no transcript messages returned")
            throw ChatServiceError.missingAssistantMessage
        }
    }

    // MARK: - Thinking Indicator

    private func startThinkingTimer() {
        thinkingStartedAt = Date()
    }

    private func stopThinkingTimer() {
        thinkingStartedAt = nil
    }

    private func isCancelledOperation(_ error: Error) -> Bool {
        Task.isCancelled || ClientFailure.classify(error) == .cancelled
    }

    // MARK: - Voice Dictation

    func checkAndRefreshVoiceDictation() async {
        await voiceInput.checkAndRefreshAvailability()
    }

    func toggleVoiceRecording() async {
        if !isRecording {
            errorMessage = nil
        }
        await voiceInput.toggle()
    }

    private func sendVoiceTranscript(_ transcript: String) async {
        let signpostState = chatPerfSignposter.beginInterval("send-voice-transcript")
        defer { chatPerfSignposter.endInterval("send-voice-transcript", signpostState) }

        errorMessage = nil
        let existingInput = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        let message = existingInput.isEmpty ? transcript : "\(existingInput) \(transcript)"
        inputText = ""
        await sendMessage(text: message)
    }
}
