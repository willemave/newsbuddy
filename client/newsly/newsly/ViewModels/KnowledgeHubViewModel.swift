//
//  KnowledgeHubViewModel.swift
//  newsly
//

import Foundation
import SwiftUI

@MainActor
protocol KnowledgeHubChatServicing: AnyObject {
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
}

extension ChatService: KnowledgeHubChatServicing {}

@MainActor
class KnowledgeHubViewModel: ObservableObject {
    @Published var sessions: [ChatSessionSummary] = []
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var hasMoreSessions = false
    @Published var isCreatingSession = false
    @Published var errorMessage: String?
    @Published var hasLoadMoreError = false
    @Published private(set) var voiceDictationAvailable = false
    @Published private(set) var isVoiceRecording = false
    @Published private(set) var isVoiceTranscribing = false
    @Published private(set) var isVoiceActionInFlight = false
    @Published private(set) var completedVoiceRoute: ChatSessionRoute?

    private let chatService: any KnowledgeHubChatServicing
    private let transcriptionService: any SpeechTranscribing
    private var nextCursor: String?
    private let historyPageLimit = 20
    private var pendingVoiceTranscript: String?
    private var hasConfiguredVoiceCallbacks = false

    init(
        chatService: any KnowledgeHubChatServicing = ChatService.shared,
        transcriptionService: (any SpeechTranscribing)? = nil,
        initialVoiceDictationAvailable: Bool = false
    ) {
        self.chatService = chatService
        self.transcriptionService = transcriptionService ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.voiceDictationAvailable = initialVoiceDictationAvailable
    }

    func loadHub() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }

        errorMessage = nil
        hasLoadMoreError = false

        do {
            let response = try await chatService.listSessionsPage(
                contentId: nil,
                newsItemId: nil,
                limit: historyPageLimit,
                cursor: nil
            )
            sessions = response.sessions
            nextCursor = response.meta.nextCursor
            hasMoreSessions = response.meta.hasMore
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            nextCursor = nil
            hasMoreSessions = false
            errorMessage = error.localizedDescription
        }
    }

    func loadMoreSessions() async {
        guard !isLoading, !isLoadingMore, hasMoreSessions, let cursor = nextCursor else {
            return
        }

        isLoadingMore = true
        hasLoadMoreError = false
        defer { isLoadingMore = false }

        do {
            let response = try await chatService.listSessionsPage(
                contentId: nil,
                newsItemId: nil,
                limit: historyPageLimit,
                cursor: cursor
            )
            appendUniqueSessions(response.sessions)
            nextCursor = response.meta.nextCursor
            hasMoreSessions = response.meta.hasMore
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            hasLoadMoreError = true
        }
    }

    func startSearchChat(message: String) async -> ChatSessionRoute? {
        await startHubAssistantTurn(message: message)
    }

    func checkAndRefreshVoiceDictation() async {
        if transcriptionService.isAvailable {
            voiceDictationAvailable = true
            return
        }

        voiceDictationAvailable = await OpenAIService.shared.refreshTranscriptionAvailability()
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
        guard hasConfiguredVoiceCallbacks || isVoiceRecording || isVoiceTranscribing || pendingVoiceTranscript != nil else {
            return
        }
        transcriptionService.reset()
        hasConfiguredVoiceCallbacks = false
        pendingVoiceTranscript = nil
        isVoiceRecording = false
        isVoiceTranscribing = false
        isVoiceActionInFlight = false
    }

    func startSummaryChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: (
                "Give me a summary of the last day's content from my feed, "
                + "including recent news items and articles. "
                + "What are the key themes and most important takeaways?"
            ),
            screenContext: makeHubContext(
                query: "recent news items and articles from my feed",
                note: (
                    "Summarize recent in-app feed content. Include both short-form news "
                    + "items and longer articles. Prefer in-app content before web search."
                )
            )
        )
    }

    func startCommentsChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: (
                "What are the most interesting and insightful comments from the "
                + "news items and articles in my feed recently? "
                + "Highlight any surprising perspectives or debates."
            )
        )
    }

    func startInterestingUnreadNewsChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: InterestingUnreadNewsAssistantAction.prompt,
            screenContext: InterestingUnreadNewsAssistantAction.screenContext(
                screenType: "knowledge_hub",
                screenTitle: "Knowledge"
            )
        )
    }

    func startFindArticlesChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: "Find a few new articles or sources I should read next based on what I've been reading."
        )
    }

    func startFindFeedsChat() async -> ChatSessionRoute? {
        await startHubAssistantTurn(
            message: "Recommend a few feeds, newsletters, or podcasts I should add based on what I've been reading."
        )
    }

    private func startHubAssistantTurn(
        message: String,
        screenContext: AssistantScreenContext? = nil
    ) async -> ChatSessionRoute? {
        guard !isCreatingSession else { return nil }
        isCreatingSession = true
        errorMessage = nil
        defer { isCreatingSession = false }

        do {
            let response = try await chatService.createAssistantTurn(
                message: message,
                sessionId: nil,
                screenContext: screenContext ?? makeHubContext()
            )
            prependSession(response.session)
            return ChatSessionRoute(
                sessionId: response.session.id,
                initialUserMessageText: response.userMessage.content,
                initialUserMessageTimestamp: response.userMessage.timestamp,
                pendingMessageId: response.messageId
            )
        } catch where isNetworkCancellation(error) {
            return nil
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    private func makeHubContext(
        query: String? = nil,
        note: String? = nil
    ) -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "knowledge_hub",
            screenTitle: "Knowledge",
            query: query,
            note: note
        )
    }

    private func appendUniqueSessions(_ newSessions: [ChatSessionSummary]) {
        var seenIds = Set(sessions.map(\.id))
        for session in newSessions where seenIds.insert(session.id).inserted {
            sessions.append(session)
        }
    }

    private func prependSession(_ session: ChatSessionSummary) {
        sessions.removeAll { $0.id == session.id }
        sessions.insert(session, at: 0)
    }

    private func startVoiceRecording() async {
        if !voiceDictationAvailable {
            await checkAndRefreshVoiceDictation()
        }
        guard voiceDictationAvailable else {
            errorMessage = "Microphone is unavailable right now. Try again in a moment."
            return
        }

        configureTranscriptionCallbacks()
        pendingVoiceTranscript = nil
        errorMessage = nil
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            try await transcriptionService.start()
            isVoiceRecording = true
            isVoiceTranscribing = false
        } catch {
            errorMessage = error.localizedDescription
            isVoiceRecording = false
            isVoiceTranscribing = false
        }
    }

    private func stopVoiceRecordingAndStartChat() async -> ChatSessionRoute? {
        guard isVoiceRecording else { return nil }
        isVoiceActionInFlight = true
        defer { isVoiceActionInFlight = false }

        do {
            let transcript = try await transcriptionService.stop()
            pendingVoiceTranscript = nil
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
        return await startHubAssistantTurn(message: trimmedTranscript)
    }

    private func configureTranscriptionCallbacks() {
        hasConfiguredVoiceCallbacks = true
        transcriptionService.onTranscriptDelta = nil
        transcriptionService.onTranscriptFinal = { [weak self] transcript in
            self?.pendingVoiceTranscript = transcript
        }
        transcriptionService.onStopReason = { [weak self] reason in
            guard let self else { return }
            switch reason {
            case .manual:
                return
            case .silenceAutoStop:
                let transcript = self.pendingVoiceTranscript ?? ""
                self.pendingVoiceTranscript = nil
                self.isVoiceRecording = false
                self.isVoiceTranscribing = false
                self.isVoiceActionInFlight = true
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    let route = await self.submitVoiceTranscript(transcript)
                    self.isVoiceActionInFlight = false
                    if let route {
                        self.completedVoiceRoute = route
                    }
                }
            case .cancel, .failure:
                self.pendingVoiceTranscript = nil
                self.isVoiceRecording = false
                self.isVoiceTranscribing = false
                self.isVoiceActionInFlight = false
            }
        }
        transcriptionService.onError = { [weak self] message in
            self?.errorMessage = message
            self?.pendingVoiceTranscript = nil
            self?.isVoiceRecording = false
            self?.isVoiceTranscribing = false
            self?.isVoiceActionInFlight = false
        }
        transcriptionService.onStateChange = { [weak self] state in
            guard let self else { return }
            switch state {
            case .idle:
                self.isVoiceRecording = false
                self.isVoiceTranscribing = false
            case .recording:
                self.isVoiceRecording = true
                self.isVoiceTranscribing = false
            case .transcribing:
                self.isVoiceRecording = false
                self.isVoiceTranscribing = true
            }
        }
    }
}
