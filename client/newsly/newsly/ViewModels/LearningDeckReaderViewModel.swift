//
//  LearningDeckReaderViewModel.swift
//  newsly
//

import Foundation
import Observation

private enum LearningDeckReaderTaskKey: Hashable {
    case send
    case viewer
}

protocol LearningDeckReaderChatServicing: MessageStatusFetching {
    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse
}

extension ChatService: LearningDeckReaderChatServicing {}

struct LearningDeckSlideContext: Equatable {
    var horizontalIndex: Int?
    var verticalIndex: Int?
    var totalSlides: Int?
    var title: String?
    var text: String?

    static let empty = LearningDeckSlideContext()

    init(
        horizontalIndex: Int? = nil,
        verticalIndex: Int? = nil,
        totalSlides: Int? = nil,
        title: String? = nil,
        text: String? = nil
    ) {
        self.horizontalIndex = horizontalIndex
        self.verticalIndex = verticalIndex
        self.totalSlides = totalSlides
        self.title = nonEmptyTrimmed(title)
        self.text = nonEmptyTrimmed(text)
    }

    init?(scriptPayload: Any) {
        guard let payload = scriptPayload as? [String: Any] else { return nil }
        self.init(
            horizontalIndex: LearningDeckSlideContext.integerValue(payload["h"]),
            verticalIndex: LearningDeckSlideContext.integerValue(payload["v"]),
            totalSlides: LearningDeckSlideContext.integerValue(payload["total"]),
            title: payload["title"] as? String,
            text: payload["text"] as? String
        )
    }

    private static func integerValue(_ value: Any?) -> Int? {
        if let value = value as? Int {
            return value
        }
        if let value = value as? Double {
            return Int(value)
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        return nil
    }
}

enum LearningDeckViewerFailureKind: Equatable {
    case generation
    case viewerResolution
}

@MainActor
@Observable
final class LearningDeckReaderViewModel {
    var inputText = ""
    var timeline: [ChatTimelineItem] = []
    var currentSlideContext = LearningDeckSlideContext.empty
    var session: ChatSessionSummary?
    var isSending = false
    var errorMessage: String?
    var thinkingStartedAt: Date?

    var hasVisiblePartialResponse: Bool {
        timeline.contains { $0.message.isVisiblePartialResponse }
    }

    // Viewer resolution (generating state) — populated when the deck is still
    // being generated and has no viewer URL yet.
    var resolvedViewerURL: URL?
    var isResolvingViewer = false
    var viewerFailureKind: LearningDeckViewerFailureKind?
    var isRetryingGeneration = false
    var generationStatusLabel = "Preparing your deck"
    var generationNote: String?

    var viewerResolutionFailed: Bool {
        viewerFailureKind != nil
    }

    var canRetryGeneration: Bool {
        viewerFailureKind == .generation
    }

    var viewerFailureActionTitle: String {
        switch viewerFailureKind {
        case .generation:
            return isRetryingGeneration ? "Trying again…" : "Try again"
        case .viewerResolution, .none:
            return "Reconnect"
        }
    }

    private let deck: LearningDeck
    private let chatService: LearningDeckReaderChatServicing
    private let messageCompletionRegistry: ChatMessageCompletionRegistry
    private let deckService: any LearningDeckServicing
    private let deckStatusRegistry: LearningDeckStatusRegistry
    private let lifecycle: AppLifecycle

    @ObservationIgnored
    private let tasks = TaskBag<LearningDeckReaderTaskKey>()
    @ObservationIgnored
    private var isRouteVisible = true
    @ObservationIgnored
    private var isViewActive: Bool
    @ObservationIgnored
    private var sendQueue = PendingSendQueue()

    init(
        lifecycle: AppLifecycle,
        deck: LearningDeck,
        chatService: any LearningDeckReaderChatServicing,
        messageCompletionRegistry: ChatMessageCompletionRegistry,
        deckService: any LearningDeckServicing,
        deckStatusRegistry: LearningDeckStatusRegistry? = nil,
        viewerPollIntervalNanoseconds: UInt64 = 3_000_000_000,
        viewerPollAttemptLimit: Int = 120
    ) {
        self.deck = deck
        self.chatService = chatService
        self.messageCompletionRegistry = messageCompletionRegistry
        self.deckService = deckService
        self.lifecycle = lifecycle
        self.isViewActive = lifecycle.phase == .active
        self.deckStatusRegistry = deckStatusRegistry ?? LearningDeckStatusRegistry(
            statusService: deckService,
            policy: .fixed(
                intervalNanoseconds: viewerPollIntervalNanoseconds,
                attemptLimit: viewerPollAttemptLimit
            )
        )
    }

    deinit {
        tasks.cancelAll()
    }

    // MARK: - Viewer resolution

    func prepareViewer(initialURL: URL?) {
        if let initialURL {
            resolvedViewerURL = initialURL
            guard isViewActive else {
                isResolvingViewer = false
                return
            }
            if deck.hasActiveLatestRun, !tasks.isRunning(.viewer) {
                startViewerResolution()
            } else {
                isResolvingViewer = false
            }
            return
        }
        guard isViewActive else { return }
        guard resolvedViewerURL == nil, !tasks.isRunning(.viewer) else { return }
        startViewerResolution()
    }

    func retryViewerResolution() {
        guard !isRetryingGeneration else { return }
        tasks.cancel(.viewer)
        resolvedViewerURL = nil
        startViewerResolution()
    }

    func retryAfterViewerFailure() {
        guard let viewerFailureKind, !isRetryingGeneration else { return }
        switch viewerFailureKind {
        case .generation:
            startGenerationRetry()
        case .viewerResolution:
            retryViewerResolution()
        }
    }

    func cancelViewerResolution() {
        tasks.cancel(.viewer)
        isResolvingViewer = false
        isRetryingGeneration = false
    }

    private func startViewerResolution() {
        isResolvingViewer = true
        viewerFailureKind = nil
        tasks.runReplacing(.viewer) { [weak self] in
            await self?.resolveViewerLoop()
        }
    }

    private func startGenerationRetry() {
        guard !isRetryingGeneration else { return }
        isRetryingGeneration = true
        tasks.runReplacing(.viewer) { [weak self] in
            await self?.retryGenerationAndResolveViewer()
        }
    }

    private func retryGenerationAndResolveViewer() async {
        do {
            let retried = try await deckService.retryDeck(deckId: deck.id)
            try Task.checkCancellation()
            await deckStatusRegistry.invalidate(deckId: deck.id)
            applyGenerationStatus(retried)
            viewerFailureKind = nil
            isRetryingGeneration = false
            guard isViewActive else {
                isResolvingViewer = false
                return
            }
            isResolvingViewer = true
            await resolveViewerLoop()
        } catch where ClientFailure.classify(error) == .cancelled {
            isRetryingGeneration = false
            isResolvingViewer = false
        } catch {
            isRetryingGeneration = false
            isResolvingViewer = false
            viewerFailureKind = .generation
            generationNote = error.localizedDescription
        }
    }

    private func resolveViewerLoop() async {
        do {
            let latest = try await deckStatusRegistry.waitUntilTerminal(
                deckId: deck.id,
                onUpdate: { [weak self] latest in
                    self?.applyGenerationStatus(latest)
                },
                onRetry: { [weak self] _ in
                    self?.generationNote = "Connection interrupted. Still trying…"
                }
            )
            applyGenerationStatus(latest)
            if latest.viewerAvailable {
                resolvedViewerURL = try await deckService.viewerURL(deckId: latest.id)
                isResolvingViewer = false
                viewerFailureKind = nil
            } else {
                isResolvingViewer = false
                viewerFailureKind = generationFailed(latest) ? .generation : .viewerResolution
            }
        } catch where ClientFailure.classify(error) == .cancelled {
            isResolvingViewer = false
        } catch LearningDeckStatusRegistryError.timeout {
            isResolvingViewer = false
            viewerFailureKind = nil
            generationStatusLabel = "Taking longer than expected"
            generationNote = "Your deck is still being prepared."
        } catch {
            isResolvingViewer = false
            viewerFailureKind = .viewerResolution
            generationNote = error.localizedDescription
        }
    }

    private func applyGenerationStatus(_ latest: LearningDeck) {
        generationStatusLabel = latest.statusLabel
        generationNote = nonEmptyTrimmed(latest.latestNote)
    }

    private func generationFailed(_ latest: LearningDeck) -> Bool {
        guard let status = latest.latestRun?.status ?? latest.status else { return false }
        switch status {
        case .failed, .cancelled:
            return true
        case .queued, .preparing, .generating, .validating, .publishing,
             .completed, .ready, .unknown:
            return false
        }
    }

    // MARK: - Chat

    func performSendMessage(text overrideText: String? = nil) {
        guard let pending = stagePendingSend(text: overrideText) else { return }
        if isSending || tasks.isRunning(.send) {
            enqueuePendingSend(pending)
            return
        }
        tasks.runReplacing(.send) { [weak self] in
            guard let self else { return }
            await self.processPendingSend(pending)
            await self.drainQueuedSends()
        }
    }

    func handleAppear() {
        isRouteVisible = true
        resumeVisibleRouteWorkIfPossible()
    }

    func handleDisappear() {
        isRouteVisible = false
        isViewActive = false
        cancelViewerResolution()

        if pendingForegroundMessageId != nil {
            tasks.cancel(.send)
            isSending = false
            thinkingStartedAt = nil
        }
    }

    func handleLifecyclePhaseChange(initialViewerURL: URL?) {
        switch lifecycle.phase {
        case .active:
            let shouldResumeViewer = !isViewActive
            resumeVisibleRouteWorkIfPossible()
            if shouldResumeViewer, isViewActive {
                prepareViewer(initialURL: initialViewerURL)
            }
        case .inactive:
            break
        case .background:
            guard isRouteVisible else { return }
            suspendVisibleRouteForBackground()
        }
    }

    func resumeAfterActivationIfNeeded(initialViewerURL: URL?) {
        guard isRouteVisible, lifecycle.phase == .active else { return }
        resumeVisibleRouteWorkIfPossible()
        prepareViewer(initialURL: initialViewerURL)
    }

    private func resumeVisibleRouteWorkIfPossible() {
        guard isRouteVisible, lifecycle.phase == .active else { return }
        isViewActive = true
        resumeAcceptedSendIfNeeded()
        startQueuedSendDrainIfPossible()
    }

    private func suspendVisibleRouteForBackground() {
        isViewActive = false

        if pendingForegroundMessageId != nil {
            tasks.cancel(.send)
            isSending = false
            thinkingStartedAt = nil
        }

        // A generation retry is a command. Let its acknowledgement finish, then
        // defer status observation until the route is active again.
        if !isRetryingGeneration {
            cancelViewerResolution()
        }
    }

    private func stagePendingSend(text overrideText: String?) -> PendingSend? {
        let resolvedText = (overrideText ?? inputText).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !resolvedText.isEmpty else { return nil }

        if overrideText == nil {
            inputText = ""
        }
        let pending = PendingSend(
            localId: UUID(),
            text: resolvedText,
            messageId: nil,
            createdAt: Date()
        )
        upsertDeckPendingSend(pending)
        return pending
    }

    private func enqueuePendingSend(_ pending: PendingSend) {
        sendQueue.enqueue(pending)
        upsertDeckPendingSend(pending)
    }

    private func drainQueuedSends() async {
        while isViewActive, !Task.isCancelled, let pending = sendQueue.dequeue() {
            await processPendingSend(pending)
        }
    }

    private func startQueuedSendDrainIfPossible() {
        guard
            isViewActive,
            !isSending,
            pendingForegroundMessageId == nil,
            !sendQueue.isEmpty,
            !tasks.isRunning(.send)
        else {
            return
        }
        tasks.runReplacing(.send) { [weak self] in
            await self?.drainQueuedSends()
        }
    }

    private func upsertDeckPendingSend(_ pending: PendingSend) {
        upsertTimelineItem(
            ChatTimelineItem(
                id: .local(pending.localId),
                message: pending.placeholderMessage,
                pendingMessageId: pending.messageId,
                retryText: pending.text
            )
        )
    }

    private func processPendingSend(_ pendingSend: PendingSend) async {
        errorMessage = nil
        isSending = true
        thinkingStartedAt = Date()

        let localId = pendingSend.localId
        let resolvedText = pendingSend.text
        upsertDeckPendingSend(pendingSend)

        defer {
            isSending = false
            thinkingStartedAt = nil
        }

        do {
            let response = try await chatService.createAssistantTurn(
                message: resolvedText,
                sessionId: session?.id,
                screenContext: screenContext()
            )
            session = response.session
            upsertTimelineItem(
                ChatTimelineItem(
                    id: .local(localId),
                    message: response.userMessage,
                    pendingMessageId: response.messageId,
                    retryText: nil
                )
            )
            if !isViewActive {
                return
            }
            let assistantMessage = try await messageCompletionRegistry.waitForCompletion(
                messageId: response.messageId,
                onPartial: { [weak self] update in
                    self?.applyPartialResponse(update, messageId: response.messageId)
                }
            )
            upsertTimelineItem(
                ChatTimelineItem(
                    id: ChatTimelineID.server(for: assistantMessage),
                    message: assistantMessage,
                    pendingMessageId: nil,
                    retryText: nil
                )
            )
            clearPendingMessageId(for: .local(localId))
        } catch where ClientFailure.classify(error) == .cancelled {
            if pendingMessageId(for: .local(localId)) == nil {
                removeTimelineItem(id: .local(localId))
            }
        } catch {
            removePartialAssistantMessage(messageId: pendingMessageId(for: .local(localId)))
            errorMessage = error.localizedDescription
            upsertTimelineItem(
                ChatTimelineItem(
                    id: .local(localId),
                    message: ChatMessage(
                        id: Self.localMessageId(for: localId),
                        role: .user,
                        timestamp: pendingSend.createdAt,
                        content: resolvedText,
                        status: .failed,
                        error: error.localizedDescription
                    ),
                    pendingMessageId: nil,
                    retryText: resolvedText
                )
            )
        }
    }

    private func resumeAcceptedSendIfNeeded() {
        guard !tasks.isRunning(.send), let messageId = pendingForegroundMessageId else { return }

        isSending = true
        thinkingStartedAt = Date()
        tasks.runReplacing(.send) { [weak self] in
            guard let self else { return }
            await self.resumePolling(messageId: messageId)
            await self.drainQueuedSends()
        }
    }

    private func resumePolling(messageId: Int) async {
        defer {
            isSending = false
            thinkingStartedAt = nil
        }

        do {
            let assistantMessage = try await messageCompletionRegistry.waitForCompletion(
                messageId: messageId,
                onPartial: { [weak self] update in
                    self?.applyPartialResponse(update, messageId: messageId)
                }
            )
            upsertTimelineItem(
                ChatTimelineItem(
                    id: ChatTimelineID.server(for: assistantMessage),
                    message: assistantMessage,
                    pendingMessageId: nil,
                    retryText: nil
                )
            )
            clearPendingMessageId(forPendingMessageId: messageId)
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            removePartialAssistantMessage(messageId: messageId)
            errorMessage = error.localizedDescription
            markPendingMessageFailed(messageId: messageId, error: error.localizedDescription)
        }
    }

    private func screenContext() -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "learning_deck",
            screenTitle: deck.displayTitle,
            contentId: deck.sourceContentId,
            selectedTopic: "Learning Deck",
            note: compactContextNote()
        )
    }

    private func compactContextNote() -> String {
        var lines = ["Deck: \(deck.displayTitle)"]

        if let sourceTitle = nonEmptyTrimmed(deck.sourceTitle), sourceTitle != deck.displayTitle {
            lines.append("Source: \(sourceTitle)")
        }
        if let sourceURL = nonEmptyTrimmed(deck.sourceURL) {
            lines.append("URL: \(sourceURL)")
        }
        if let position = currentSlidePosition {
            lines.append("Current slide: \(position)")
        }
        if let title = nonEmptyTrimmed(currentSlideContext.title) {
            lines.append("Slide title: \(title)")
        }
        if let text = nonEmptyTrimmed(currentSlideContext.text) {
            lines.append("Slide text: \(text)")
        }

        return lines.joined(separator: "\n")
    }

    private var currentSlidePosition: String? {
        guard currentSlideContext.horizontalIndex != nil || currentSlideContext.verticalIndex != nil else {
            return nil
        }
        let horizontal = (currentSlideContext.horizontalIndex ?? 0) + 1
        if let verticalIndex = currentSlideContext.verticalIndex, verticalIndex > 0 {
            return "\(horizontal).\(verticalIndex + 1)"
        }
        return "\(horizontal)"
    }

    private func upsertTimelineItem(_ item: ChatTimelineItem) {
        var replacement = item
        if case .local(let localId) = item.id {
            replacement.isQueued = sendQueue.contains(localId: localId)
        }
        if let existingIndex = timeline.firstIndex(where: { $0.id == item.id }) {
            timeline[existingIndex] = replacement
            return
        }
        timeline.append(replacement)
        timeline.sort { $0.isOrderedBefore($1) }
    }

    private func removeTimelineItem(id: ChatTimelineID) {
        timeline.removeAll { $0.id == id }
    }

    private func applyPartialResponse(
        _ update: ChatPartialResponseUpdate,
        messageId: Int
    ) {
        if let message = update.message {
            upsertTimelineItem(
                ChatTimelineItem(
                    id: ChatTimelineID.server(for: message),
                    message: message,
                    pendingMessageId: messageId,
                    retryText: nil
                )
            )
            return
        }
        removePartialAssistantMessage(messageId: messageId)
    }

    private func removePartialAssistantMessage(messageId: Int?) {
        guard let messageId else { return }
        let partialId = ChatTimelineID.server(
            sourceMessageId: messageId,
            role: .assistant,
            displayType: .message
        )
        timeline.removeAll { item in
            item.id == partialId && item.message.isProcessing
        }
    }

    private var pendingForegroundMessageId: Int? {
        timeline.first { $0.pendingMessageId != nil }?.pendingMessageId
    }

    private func pendingMessageId(for id: ChatTimelineID) -> Int? {
        timeline.first { $0.id == id }?.pendingMessageId
    }

    private func clearPendingMessageId(for id: ChatTimelineID) {
        guard var item = timeline.first(where: { $0.id == id }) else { return }
        item.pendingMessageId = nil
        upsertTimelineItem(item)
    }

    private func clearPendingMessageId(forPendingMessageId messageId: Int) {
        guard var item = timeline.first(where: { $0.pendingMessageId == messageId }) else { return }
        item.pendingMessageId = nil
        upsertTimelineItem(item)
    }

    private func markPendingMessageFailed(messageId: Int, error: String) {
        guard let item = timeline.first(where: { $0.pendingMessageId == messageId }) else { return }
        upsertTimelineItem(
            ChatTimelineItem(
                id: item.id,
                message: ChatMessage(
                    id: item.message.id,
                    sourceMessageId: item.message.sourceMessageId,
                    displayKey: item.message.displayKey,
                    role: item.message.role,
                    timestamp: item.message.timestamp,
                    content: item.message.content,
                    displayType: item.message.displayType,
                    processLabel: item.message.processLabel,
                    status: .failed,
                    error: error,
                    feedOptions: item.message.feedOptions,
                    councilCandidates: item.message.councilCandidates,
                    activeCouncilChildSessionId: item.message.activeCouncilChildSessionId
                ),
                pendingMessageId: nil,
                retryText: item.retryText ?? item.message.content
            )
        )
    }

    private static func localMessageId(for id: UUID) -> Int {
        Int(id.uuidString.prefix(8), radix: 16) ?? 0
    }

}
