//
//  LearningHubViewModel.swift
//  newsly
//

import Foundation
import Observation
import SwiftUI

@MainActor
protocol LearningHubChatServicing: AnyObject {
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

extension ChatService: LearningHubChatServicing {}

@MainActor
@Observable
final class LearningHubViewModel {
    var sessions: [ChatSessionSummary] = []
    var isLoading = false
    var isLoadingMore = false
    var hasMoreSessions = false
    var isCreatingSession = false
    var errorMessage: String?
    var hasLoadMoreError = false
    private(set) var voiceDictationAvailable = false
    private(set) var isVoiceRecording = false
    private(set) var isVoiceTranscribing = false
    private(set) var isVoiceActionInFlight = false
    private(set) var completedVoiceRoute: ChatSessionRoute?

    @ObservationIgnored
    private let chatService: any LearningHubChatServicing
    @ObservationIgnored
    private let transcriptionService: any SpeechTranscribing
    @ObservationIgnored
    private let voiceCoordinator: VoiceDictationCoordinator
    @ObservationIgnored
    private let refreshTranscriptionAvailability: () async -> Bool
    @ObservationIgnored
    private var nextCursor: String?
    @ObservationIgnored
    private let historyPageLimit = 20
    @ObservationIgnored
    private var pendingVoiceTranscript: String?
    @ObservationIgnored
    private var hasConfiguredVoiceCallbacks = false

    init(
        chatService: any LearningHubChatServicing,
        transcriptionService: (any SpeechTranscribing)? = nil,
        refreshTranscriptionAvailability: @escaping () async -> Bool = { false },
        initialVoiceDictationAvailable: Bool = false
    ) {
        let resolvedTranscriptionService = transcriptionService ?? SpeechTranscriberFactory.makeVoiceDictationTranscriber()
        self.chatService = chatService
        self.transcriptionService = resolvedTranscriptionService
        self.voiceCoordinator = VoiceDictationCoordinator(transcriber: resolvedTranscriptionService)
        self.refreshTranscriptionAvailability = refreshTranscriptionAvailability
        self.voiceDictationAvailable = initialVoiceDictationAvailable
    }

    func loadLearning() async {
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

    func startChat(message: String) async -> ChatSessionRoute? {
        await startAssistantTurn(message: message)
    }

    func checkAndRefreshVoiceDictation() async {
        if transcriptionService.isAvailable {
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
        guard hasConfiguredVoiceCallbacks || isVoiceRecording || isVoiceTranscribing || pendingVoiceTranscript != nil else {
            return
        }
        voiceCoordinator.stopListening()
        transcriptionService.reset()
        hasConfiguredVoiceCallbacks = false
        pendingVoiceTranscript = nil
        isVoiceRecording = false
        isVoiceTranscribing = false
        isVoiceActionInFlight = false
    }

    private func startAssistantTurn(
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
                screenContext: screenContext ?? makeLearningContext()
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

    private func makeLearningContext(
        query: String? = nil,
        note: String? = nil
    ) -> AssistantScreenContext {
        AssistantScreenContext(
            screenType: "learning",
            screenTitle: "Learning",
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
        return await startAssistantTurn(message: trimmedTranscript)
    }

    private func configureTranscriptionCallbacks() {
        hasConfiguredVoiceCallbacks = true
        voiceCoordinator.listen(
            onTranscriptFinal: { [weak self] transcript in
                self?.pendingVoiceTranscript = transcript
            },
            onError: { [weak self] message in
                self?.errorMessage = message
                self?.pendingVoiceTranscript = nil
                self?.isVoiceRecording = false
                self?.isVoiceTranscribing = false
                self?.isVoiceActionInFlight = false
            },
            onStopReason: { [weak self] reason in
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
                    let route = await self.submitVoiceTranscript(transcript)
                    self.isVoiceActionInFlight = false
                    if let route {
                        self.completedVoiceRoute = route
                    }
                case .cancel, .failure:
                    self.pendingVoiceTranscript = nil
                    self.isVoiceRecording = false
                    self.isVoiceTranscribing = false
                    self.isVoiceActionInFlight = false
                }
            }
        )
    }
}
