//
//  KnowledgeChatViewModel.swift
//  newsly
//

import Foundation
import Observation

@MainActor
protocol KnowledgeChatServicing: AnyObject {
    func listSessionsPage(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int,
        cursor: String?
    ) async throws -> ChatSessionListResponse

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse

    func deleteSession(sessionId: Int) async throws

    /// The source's latest Knowledge chat, if any.
    func existingSourceChat(for source: KnowledgeSource) async throws -> ChatSessionSummary?

    func startSourceChat(for source: KnowledgeSource) async throws -> ChatSessionSummary
}

extension ChatService: KnowledgeChatServicing {
    func existingSourceChat(for source: KnowledgeSource) async throws -> ChatSessionSummary? {
        switch source {
        case .content(let id): try await getSessionForContent(contentId: id)
        case .news(let id): try await getSessionForNewsItem(newsItemId: id)
        }
    }

    func startSourceChat(for source: KnowledgeSource) async throws -> ChatSessionSummary {
        switch source {
        case .content(let id): try await startArticleChat(contentId: id)
        case .news(let id): try await startNewsChat(newsItemId: id)
        }
    }
}

@MainActor
@Observable
final class KnowledgeChatViewModel {
    private struct LocallyCreatedSession {
        let revision: Int
        let session: ChatSessionSummary
    }

    var sessions: [ChatSessionSummary] {
        get { feed.items }
        set { feed.replaceItems(newValue) }
    }

    var isLoading: Bool {
        feed.isRequestInFlight && feed.phase != .loadingMore
    }

    var isLoadingMore: Bool {
        feed.phase == .loadingMore
    }

    var hasMoreSessions: Bool {
        feed.hasMore
    }
    var isCreatingSession = false
    private(set) var loadErrorMessage: String?
    var errorMessage: String?
    var hasLoadMoreError = false
    private(set) var completedVoiceRoute: ChatSessionRoute?

    var voiceDictationAvailable: Bool { voiceInput.isAvailable }
    var isVoiceRecording: Bool { voiceInput.isRecording }
    var isVoiceTranscribing: Bool { voiceInput.isTranscribing }
    var isVoiceActionInFlight: Bool { voiceInput.isActionInFlight }

    @ObservationIgnored
    private let chatService: any KnowledgeChatServicing
    @ObservationIgnored
    private let voiceInput: ChatVoiceInputController
    @ObservationIgnored
    private var feed: PaginatedFeed<ChatSessionSummary>!
    @ObservationIgnored
    private let historyPageLimit = 20
    @ObservationIgnored
    private var deletingSessionIDs: Set<Int> = []
    @ObservationIgnored
    private var deletedSessionIDs: Set<Int> = []
    @ObservationIgnored
    private var inFlightSessionListRequestCount = 0
    @ObservationIgnored
    private var sessionMutationRevision = 0
    @ObservationIgnored
    private var locallyCreatedSessions: [Int: LocallyCreatedSession] = [:]
    @ObservationIgnored
    private let activeChatPollIntervalNanoseconds: UInt64

    var hasActiveChatWork: Bool {
        sessions.contains { $0.isPreparingChat || $0.isProcessing }
    }

    init(
        chatService: any KnowledgeChatServicing,
        transcriptionService: any SpeechTranscribing,
        refreshTranscriptionAvailability: @escaping () async -> Bool = { false },
        initialVoiceDictationAvailable: Bool = false,
        activeChatPollIntervalNanoseconds: UInt64 = 3_000_000_000
    ) {
        self.chatService = chatService
        self.voiceInput = ChatVoiceInputController(
            transcriptionService: transcriptionService,
            refreshAvailability: refreshTranscriptionAvailability,
            initiallyAvailable: initialVoiceDictationAvailable
        )
        self.activeChatPollIntervalNanoseconds = activeChatPollIntervalNanoseconds
        feed = PaginatedFeed(loadPageWithGeneration: { [weak self] cursor, generation in
            guard let self else {
                return Page(items: [], nextCursor: nil, hasMore: false)
            }
            return try await self.loadHistoryPage(cursor: cursor, generation: generation)
        })
        voiceInput.configure(
            onTranscriptReady: { [weak self] transcript in
                guard let self else { return }
                completedVoiceRoute = await startAssistantTurn(message: transcript)
            },
            onError: { [weak self] message in
                self?.errorMessage = message
            }
        )
    }

    func loadChats() async {
        guard !feed.isRequestInFlight else { return }

        loadErrorMessage = nil
        hasLoadMoreError = false
        await feed.refresh()
        loadErrorMessage = feedErrorMessage
    }

    func cancelAutomaticRead() {
        feed.cancelRequestRetainingState()
    }

    func clearError() {
        errorMessage = nil
    }

    func pollActiveChatWork() async {
        guard hasActiveChatWork else { return }

        while hasActiveChatWork, !Task.isCancelled {
            do {
                try await Task.sleep(nanoseconds: activeChatPollIntervalNanoseconds)
            } catch {
                return
            }
            guard !Task.isCancelled else { return }
            await loadChats()
        }
    }

    func loadMoreSessions() async {
        guard !feed.isRequestInFlight, hasMoreSessions, feed.nextCursor != nil else {
            return
        }

        hasLoadMoreError = false
        await feed.loadNextPage()
        hasLoadMoreError = feedErrorMessage != nil
    }

    func startChat(message: String) async -> ChatSessionRoute? {
        await startAssistantTurn(message: message)
    }

    /// Resumes the source's latest Knowledge chat, or starts one with the composer focused.
    func openSourceChat(for source: KnowledgeSource) async -> ChatSessionRoute? {
        guard let resolved = await resolveSourceChat(for: source) else { return nil }
        return ChatSessionRoute(session: resolved.session, focusComposerOnAppear: resolved.isNew)
    }

    func deleteSession(_ session: ChatSessionSummary) async {
        guard sessions.contains(where: { $0.id == session.id }),
              deletingSessionIDs.insert(session.id).inserted else {
            return
        }
        defer { deletingSessionIDs.remove(session.id) }
        errorMessage = nil

        do {
            try await chatService.deleteSession(sessionId: session.id)
            deletedSessionIDs.insert(session.id)
            locallyCreatedSessions.removeValue(forKey: session.id)
            feed.replaceItems(sessions.filter { $0.id != session.id })
            discardSettledDeletionTombstones()
        } catch where ClientFailure.classify(error) == .cancelled {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func checkAndRefreshVoiceDictation() async {
        await voiceInput.checkAndRefreshAvailability()
    }

    func toggleVoiceRecording() async {
        guard !isCreatingSession else { return }
        errorMessage = nil
        await voiceInput.toggle()
    }

    func clearCompletedVoiceRoute() {
        completedVoiceRoute = nil
    }

    func cancelVoiceRecording() {
        voiceInput.reset()
    }

    private func startAssistantTurn(message: String) async -> ChatSessionRoute? {
        guard !isCreatingSession else { return nil }
        isCreatingSession = true
        errorMessage = nil
        defer { isCreatingSession = false }

        do {
            let response = try await chatService.createAssistantTurn(
                message: message,
                sessionId: nil,
                screenContext: makeKnowledgeContext()
            )
            prependSession(response.session)
            return ChatSessionRoute(
                sessionId: response.session.id,
                initialUserMessageText: response.userMessage.content,
                initialUserMessageTimestamp: response.userMessage.timestamp,
                pendingMessageId: response.messageId
            )
        } catch where ClientFailure.classify(error) == .cancelled {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    private func resolveSourceChat(
        for source: KnowledgeSource
    ) async -> (session: ChatSessionSummary, isNew: Bool)? {
        guard !isCreatingSession else { return nil }
        isCreatingSession = true
        errorMessage = nil
        defer { isCreatingSession = false }

        do {
            if let existing = try await chatService.existingSourceChat(for: source) {
                return (existing, false)
            }
            let session = try await chatService.startSourceChat(for: source)
            prependSession(session)
            return (session, true)
        } catch where ClientFailure.classify(error) == .cancelled {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    private func makeKnowledgeContext() -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "knowledge_hub",
            screenTitle: "Knowledge",
            query: nil,
            note: nil
        )
    }

    private func prependSession(_ session: ChatSessionSummary) {
        deletedSessionIDs.remove(session.id)
        sessionMutationRevision &+= 1
        locallyCreatedSessions[session.id] = LocallyCreatedSession(
            revision: sessionMutationRevision,
            session: session
        )
        var updatedSessions = sessions.filter { $0.id != session.id }
        updatedSessions.insert(session, at: 0)
        feed.replaceItems(updatedSessions)
    }

    private var feedErrorMessage: String? {
        guard case .error(let message) = feed.phase else { return nil }
        return message
    }

    private func loadHistoryPage(
        cursor: String?,
        generation: Int
    ) async throws -> Page<ChatSessionSummary> {
        let requestStartRevision = sessionMutationRevision
        beginSessionListRequest()
        defer { finishSessionListRequest() }

        let response = try await chatService.listSessionsPage(
            contentId: nil,
            newsItemId: nil,
            limit: historyPageLimit,
            cursor: cursor
        )
        let loadedSessions: [ChatSessionSummary]
        if cursor == nil, feed.isCurrentRequest(generation) {
            loadedSessions = reconcileInitialSessions(
                response.sessions,
                requestStartRevision: requestStartRevision
            )
        } else {
            loadedSessions = visibleSessions(response.sessions)
        }

        return Page(
            items: loadedSessions,
            nextCursor: response.meta.nextCursor,
            hasMore: response.meta.hasMore
        )
    }

    private func reconcileInitialSessions(
        _ loadedSessions: [ChatSessionSummary],
        requestStartRevision: Int
    ) -> [ChatSessionSummary] {
        var reconciled = visibleSessions(loadedSessions)
        let loadedSessionIDs = Set(reconciled.map(\.id))

        for local in locallyCreatedSessions.values.sorted(by: { $0.revision < $1.revision }) {
            if local.revision <= requestStartRevision,
               loadedSessionIDs.contains(local.session.id) {
                locallyCreatedSessions.removeValue(forKey: local.session.id)
                continue
            }

            reconciled.removeAll { $0.id == local.session.id }
            reconciled.insert(local.session, at: 0)
        }
        return reconciled
    }

    private func visibleSessions(
        _ sessions: [ChatSessionSummary]
    ) -> [ChatSessionSummary] {
        sessions.filter { !deletedSessionIDs.contains($0.id) }
    }

    private func beginSessionListRequest() {
        inFlightSessionListRequestCount += 1
    }

    private func finishSessionListRequest() {
        inFlightSessionListRequestCount -= 1
        discardSettledDeletionTombstones()
    }

    private func discardSettledDeletionTombstones() {
        guard inFlightSessionListRequestCount == 0 else { return }
        deletedSessionIDs.removeAll()
    }


}
