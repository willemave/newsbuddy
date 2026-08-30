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
}

extension ChatService: KnowledgeChatServicing {}

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
    private(set) var voiceDictationAvailable = false
    private(set) var isVoiceRecording = false
    private(set) var isVoiceTranscribing = false
    private(set) var isVoiceActionInFlight = false
    private(set) var voiceState: SpeechTranscriptionState = .idle
    private(set) var completedVoiceRoute: ChatSessionRoute?

    @ObservationIgnored
    private let chatService: any KnowledgeChatServicing
    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let refreshTranscriptionAvailability: () async -> Bool
    @ObservationIgnored
    private var feed: PaginatedFeed<ChatSessionSummary>!
    @ObservationIgnored
    private let historyPageLimit = 20
    @ObservationIgnored
    private var pendingVoiceTranscript: String?
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
        transcriptionService: (any SpeechTranscribing)? = nil,
        refreshTranscriptionAvailability: @escaping () async -> Bool = { false },
        initialVoiceDictationAvailable: Bool = false,
        activeChatPollIntervalNanoseconds: UInt64 = 3_000_000_000
    ) {
        let resolvedTranscriptionService = transcriptionService ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.chatService = chatService
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: resolvedTranscriptionService)
        self.refreshTranscriptionAvailability = refreshTranscriptionAvailability
        self.voiceDictationAvailable = initialVoiceDictationAvailable
        self.activeChatPollIntervalNanoseconds = activeChatPollIntervalNanoseconds
        feed = PaginatedFeed(loadPageWithGeneration: { [weak self] cursor, generation in
            guard let self else {
                return Page(items: [], nextCursor: nil, hasMore: false)
            }
            return try await self.loadHistoryPage(cursor: cursor, generation: generation)
        })
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
        if voiceCoordinator.isAvailable {
            voiceDictationAvailable = true
            return
        }

        voiceDictationAvailable = await refreshTranscriptionAvailability()
    }

    func toggleVoiceRecording() async -> ChatSessionRoute? {
        guard !isCreatingSession else { return nil }
        if isVoiceRecording {
            return await stopVoiceRecordingAndStartChat()
        }
        guard !isVoiceActionInFlight, !isVoiceTranscribing else { return nil }
        await startVoiceRecording()
        return nil
    }

    func clearCompletedVoiceRoute() {
        completedVoiceRoute = nil
    }

    func cancelVoiceRecording() {
        guard voiceCoordinator.hasActiveSession || isVoiceRecording || isVoiceTranscribing || pendingVoiceTranscript != nil else {
            return
        }
        voiceCoordinator.cancel()
        pendingVoiceTranscript = nil
        voiceState = .idle
        isVoiceRecording = false
        isVoiceTranscribing = false
        isVoiceActionInFlight = false
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

    private func startVoiceRecording() async {
        if !voiceDictationAvailable {
            await checkAndRefreshVoiceDictation()
        }
        guard voiceDictationAvailable else {
            errorMessage = "Microphone is unavailable right now. Try again in a moment."
            return
        }

        pendingVoiceTranscript = nil
        errorMessage = nil
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            try await voiceCoordinator.start(
                onTranscriptFinal: { [weak self] transcript in
                    self?.pendingVoiceTranscript = transcript
                },
                onError: { [weak self] message in
                    self?.applyVoiceFailure(message)
                },
                onStateChange: { [weak self] state in
                    self?.applyVoiceState(state)
                },
                onStopReason: { [weak self] reason in
                    await self?.handleVoiceStopReason(reason)
                }
            )
        } catch {
            applyVoiceFailure(error.localizedDescription)
        }
    }

    private func stopVoiceRecordingAndStartChat() async -> ChatSessionRoute? {
        guard isVoiceRecording else { return nil }
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            voiceState = .transcribing
            isVoiceRecording = false
            isVoiceTranscribing = true
            let transcript = try await voiceCoordinator.stop()
            pendingVoiceTranscript = nil
            voiceState = .idle
            isVoiceRecording = false
            isVoiceTranscribing = false
            return await submitVoiceTranscript(transcript)
        } catch {
            errorMessage = error.localizedDescription
            isVoiceRecording = false
            isVoiceTranscribing = false
            return nil
        }
    }

    private func submitVoiceTranscript(_ transcript: String) async -> ChatSessionRoute? {
        let trimmedTranscript = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTranscript.isEmpty else {
            errorMessage = "I didn't catch that. Try again."
            return nil
        }

        errorMessage = nil
        return await startAssistantTurn(message: trimmedTranscript)
    }

    private func applyVoiceState(_ state: SpeechTranscriptionState) {
        voiceState = state
        switch state {
        case .idle, .starting:
            isVoiceRecording = false
            isVoiceTranscribing = false
        case .recording:
            isVoiceRecording = true
            isVoiceTranscribing = false
        case .transcribing:
            isVoiceRecording = false
            isVoiceTranscribing = true
        case .failed(let message):
            applyVoiceFailure(message)
        }
    }

    private func applyVoiceFailure(_ message: String) {
        voiceState = .failed(message)
        errorMessage = message
        pendingVoiceTranscript = nil
        isVoiceRecording = false
        isVoiceTranscribing = false
        isVoiceActionInFlight = false
    }

    private func handleVoiceStopReason(_ reason: SpeechStopReason) async {
        switch reason {
        case .manual:
            return
        case .silenceAutoStop, .maximumDuration:
            let transcript = pendingVoiceTranscript ?? ""
            pendingVoiceTranscript = nil
            isVoiceRecording = false
            isVoiceTranscribing = false
            isVoiceActionInFlight = true
            let route = await submitVoiceTranscript(transcript)
            isVoiceActionInFlight = false
            if let route {
                completedVoiceRoute = route
            }
        case .noSpeechTimeout:
            applyVoiceFailure("No speech detected. Try again.")
        case .cancel:
            pendingVoiceTranscript = nil
            voiceState = .idle
            isVoiceRecording = false
            isVoiceTranscribing = false
            isVoiceActionInFlight = false
        case .failure:
            isVoiceActionInFlight = false
        }
    }
}
