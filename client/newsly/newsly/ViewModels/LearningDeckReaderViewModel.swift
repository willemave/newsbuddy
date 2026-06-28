//
//  LearningDeckReaderViewModel.swift
//  newsly
//

import Foundation
import Observation

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
    private var sendTask: Task<Void, Never>?
    @ObservationIgnored
    private var viewerTask: Task<Void, Never>?

    init(
        deck: LearningDeck,
        chatService: LearningDeckReaderChatServicing = ChatService.shared,
        deckService: LearningDeckService = .shared
    ) {
        self.deck = deck
        self.chatService = chatService
        self.deckService = deckService
    }

    deinit {
        sendTask?.cancel()
        viewerTask?.cancel()
    }

    // MARK: - Viewer resolution

    func prepareViewer(initialURL: URL?) {
        if let initialURL {
            resolvedViewerURL = initialURL
            isResolvingViewer = false
            return
        }
        guard resolvedViewerURL == nil, viewerTask == nil else { return }
        startViewerResolution()
    }

    func retryViewerResolution() {
        viewerTask?.cancel()
        viewerTask = nil
        startViewerResolution()
    }

    func cancelViewerResolution() {
        viewerTask?.cancel()
        viewerTask = nil
    }

    private func startViewerResolution() {
        isResolvingViewer = true
        viewerResolutionFailed = false
        viewerTask = Task { @MainActor [weak self] in
            await self?.resolveViewerLoop()
            self?.viewerTask = nil
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
        sendTask?.cancel()
        sendTask = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.sendMessage(text: overrideText)
            self.sendTask = nil
        }
    }

    func cancelInFlightWork() {
        sendTask?.cancel()
        sendTask = nil
        isSending = false
        thinkingStartedAt = nil
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
                timestamp: Self.timestamp(),
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
            let assistantMessage = try await pollUntilComplete(messageId: response.messageId)
            upsertTimelineItem(
                ChatTimelineItem(
                    id: ChatTimelineID.server(for: assistantMessage),
                    message: assistantMessage,
                    pendingMessageId: nil,
                    retryText: nil
                )
            )
        } catch where isNetworkCancellation(error) {
            removeTimelineItem(id: .local(localId))
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

    private static func localMessageId(for id: UUID) -> Int {
        Int(id.uuidString.prefix(8), radix: 16) ?? 0
    }

    private static func timestamp() -> String {
        ISO8601DateFormatter().string(from: Date())
    }

    private static func clipped(_ value: String, maxLength: Int) -> String {
        guard value.count > maxLength else { return value }
        guard maxLength > 3 else { return String(value.prefix(maxLength)) }
        return String(value.prefix(maxLength - 3)) + "..."
    }
}
