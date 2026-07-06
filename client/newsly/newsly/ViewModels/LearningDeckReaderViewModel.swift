//
//  LearningDeckReaderViewModel.swift
//  newsly
//

import Foundation
import Observation

private enum LearningDeckForegroundPollingSuspended: Error {
    case inactive
}

private enum LearningDeckReaderTaskKey: Hashable {
    case send
    case viewer
}

protocol LearningDeckReaderChatServicing: AnyObject {
    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse

    func getMessageStatus(messageId: Int) async throws -> MessageStatusResponse
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

    // Viewer resolution (generating state) — populated when the deck is still
    // being generated and has no viewer URL yet.
    var resolvedViewerURL: URL?
    var isResolvingViewer = false
    var viewerResolutionFailed = false
    var generationStatusLabel = "Preparing your deck"
    var generationNote: String?

    private let deck: LearningDeck
    private let chatService: LearningDeckReaderChatServicing
    private let deckService: LearningDeckService
    private let maxPollingAttempts = 120
    private let pollingIntervalNanoseconds: UInt64 = 500_000_000
    private let viewerPollIntervalNanoseconds: UInt64 = 3_000_000_000
    private let viewerPollAttemptLimit = 120

    @ObservationIgnored
    private let tasks = TaskBag<LearningDeckReaderTaskKey>()
    @ObservationIgnored
    private var isViewActive = true

    init(
        deck: LearningDeck,
        chatService: any LearningDeckReaderChatServicing,
        deckService: LearningDeckService
    ) {
        self.deck = deck
        self.chatService = chatService
        self.deckService = deckService
    }

    deinit {
        tasks.cancelAll()
    }

    // MARK: - Viewer resolution

    func prepareViewer(initialURL: URL?) {
        if let initialURL {
            resolvedViewerURL = initialURL
            isResolvingViewer = false
            return
        }
        guard resolvedViewerURL == nil, !tasks.isRunning(.viewer) else { return }
        startViewerResolution()
    }

    func retryViewerResolution() {
        tasks.cancel(.viewer)
        startViewerResolution()
    }

    func cancelViewerResolution() {
        tasks.cancel(.viewer)
    }

    private func startViewerResolution() {
        isResolvingViewer = true
        viewerResolutionFailed = false
        tasks.runReplacing(.viewer) { [weak self] in
            await self?.resolveViewerLoop()
        }
    }

    private func resolveViewerLoop() async {
        var attempts = 0
        while attempts < viewerPollAttemptLimit {
            do {
                try Task.checkCancellation()
                let latest = try await deckService.fetchDeck(id: deck.id)
                generationStatusLabel = latest.statusLabel
                if let note = nonEmptyTrimmed(latest.latestNote) {
                    generationNote = note
                }

                if latest.viewerAvailable {
                    let url = try await deckService.viewerURL(deckId: latest.id)
                    resolvedViewerURL = url
                    isResolvingViewer = false
                    viewerResolutionFailed = false
                    return
                }

                if !latest.hasActiveLatestRun {
                    isResolvingViewer = false
                    viewerResolutionFailed = true
                    return
                }

                attempts += 1
                try await Task.sleep(nanoseconds: viewerPollIntervalNanoseconds)
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                generationNote = error.localizedDescription
                isResolvingViewer = false
                viewerResolutionFailed = true
                return
            }
        }
        isResolvingViewer = false
        viewerResolutionFailed = true
    }

    // MARK: - Chat

    func performSendMessage(text overrideText: String? = nil) {
        guard !isSending else { return }
        tasks.runReplacing(.send) { [weak self] in
            guard let self else { return }
            await self.sendMessage(text: overrideText)
        }
    }

    func handleAppear() {
        isViewActive = true
        resumeAcceptedSendIfNeeded()
    }

    func handleDisappear() {
        isViewActive = false

        if pendingForegroundMessageId != nil {
            tasks.cancel(.send)
            isSending = false
            thinkingStartedAt = nil
        }
    }

    func sendMessage(text overrideText: String? = nil) async {
        let resolvedText = (overrideText ?? inputText).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !resolvedText.isEmpty, !isSending else { return }

        if overrideText == nil {
            inputText = ""
        }
        errorMessage = nil
        isSending = true
        thinkingStartedAt = Date()

        let localId = UUID()
        let pending = ChatTimelineItem(
            id: .local(localId),
            message: ChatMessage(
                id: Self.localMessageId(for: localId),
                role: .user,
                timestamp: Date(),
                content: resolvedText,
                status: .processing
            ),
            pendingMessageId: nil,
            retryText: resolvedText
        )
        upsertTimelineItem(pending)

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
            let assistantMessage = try await pollUntilComplete(messageId: response.messageId)
            upsertTimelineItem(
                ChatTimelineItem(
                    id: ChatTimelineID.server(for: assistantMessage),
                    message: assistantMessage,
                    pendingMessageId: nil,
                    retryText: nil
                )
            )
            clearPendingMessageId(for: .local(localId))
        } catch is LearningDeckForegroundPollingSuspended {
            // The backend has accepted the turn. Leave the pending user row in
            // place so foregrounding can resume status polling.
        } catch where isNetworkCancellation(error) {
            if pendingMessageId(for: .local(localId)) == nil {
                removeTimelineItem(id: .local(localId))
            }
        } catch {
            errorMessage = error.localizedDescription
            upsertTimelineItem(
                ChatTimelineItem(
                    id: .local(localId),
                    message: ChatMessage(
                        id: Self.localMessageId(for: localId),
                        role: .user,
                        timestamp: pending.message.timestamp,
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

    private func pollUntilComplete(messageId: Int) async throws -> ChatMessage {
        var attempts = 0
        while attempts < maxPollingAttempts {
            try Task.checkCancellation()
            if !isViewActive {
                throw LearningDeckForegroundPollingSuspended.inactive
            }
            let status = try await chatService.getMessageStatus(messageId: messageId)
            switch status.status {
            case .completed:
                guard let assistantMessage = status.assistantMessage else {
                    throw ChatServiceError.missingAssistantMessage
                }
                return assistantMessage
            case .failed:
                throw ChatServiceError.processingFailed(status.error ?? "Unknown error")
            case .processing, .unknown(_):
                attempts += 1
                try await Task.sleep(nanoseconds: pollingIntervalNanoseconds)
            }
        }
        throw ChatServiceError.timeout
    }

    private func resumeAcceptedSendIfNeeded() {
        guard !tasks.isRunning(.send), let messageId = pendingForegroundMessageId else { return }

        isSending = true
        thinkingStartedAt = Date()
        tasks.runReplacing(.send) { [weak self] in
            guard let self else { return }
            await self.resumePolling(messageId: messageId)
        }
    }

    private func resumePolling(messageId: Int) async {
        defer {
            isSending = false
            thinkingStartedAt = nil
        }

        do {
            let assistantMessage = try await pollUntilComplete(messageId: messageId)
            upsertTimelineItem(
                ChatTimelineItem(
                    id: ChatTimelineID.server(for: assistantMessage),
                    message: assistantMessage,
                    pendingMessageId: nil,
                    retryText: nil
                )
            )
            clearPendingMessageId(forPendingMessageId: messageId)
        } catch is LearningDeckForegroundPollingSuspended {
            return
        } catch where isNetworkCancellation(error) {
            return
        } catch {
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

        return Self.clipped(lines.joined(separator: "\n"), maxLength: 500)
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
        var itemsById = Dictionary(uniqueKeysWithValues: timeline.map { ($0.id, $0) })
        itemsById[item.id] = item
        timeline = itemsById.values.sorted { $0.isOrderedBefore($1) }
    }

    private func removeTimelineItem(id: ChatTimelineID) {
        timeline.removeAll { $0.id == id }
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

    private static func clipped(_ value: String, maxLength: Int) -> String {
        guard value.count > maxLength else { return value }
        guard maxLength > 3 else { return String(value.prefix(maxLength)) }
        return String(value.prefix(maxLength - 3)) + "..."
    }
}
