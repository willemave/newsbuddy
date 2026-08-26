//
//  KnowledgeTimelineViewModel.swift
//  newsly
//

import Foundation
import Observation

enum KnowledgeTimelineFailure: Identifiable {
    case savedLoad
    case savedAction(String)
    case chatsLoad
    case chatsAction(String)
    case decksLoad
    case decksAction(String)
    case narrationsLoad
    case narrationsAction(String)

    var id: String {
        switch self {
        case .savedLoad: "saved.load"
        case .savedAction: "saved.action"
        case .chatsLoad: "chats.load"
        case .chatsAction: "chats.action"
        case .decksLoad: "decks.load"
        case .decksAction: "decks.action"
        case .narrationsLoad: "narrations.load"
        case .narrationsAction: "narrations.action"
        }
    }

    var message: String {
        switch self {
        case .savedLoad: "Saved knowledge couldn't be loaded."
        case .savedAction(let message),
             .chatsAction(let message),
             .decksAction(let message),
             .narrationsAction(let message): message
        case .chatsLoad: "Chats couldn't be loaded."
        case .decksLoad: "Learning Decks couldn't be loaded."
        case .narrationsLoad: "Narrations couldn't be loaded."
        }
    }

    var actionTitle: String {
        switch self {
        case .savedLoad, .chatsLoad, .decksLoad, .narrationsLoad: "Try Again"
        case .savedAction, .chatsAction, .decksAction, .narrationsAction: "Dismiss"
        }
    }

    var accessibilityIdentifier: String {
        "knowledge.error.\(id)"
    }
}

@MainActor
@Observable
final class KnowledgeTimelineViewModel {
    let savedContent: ContentListViewModel
    let chats: KnowledgeChatViewModel
    let decks: LearningDecksViewModel
    let narrations: CustomNarrationLibraryViewModel
    private(set) var timeline: [KnowledgeTimelineItem] = []
    private(set) var groupedTimeline: [KnowledgeTimelineDayGroup] = []

    init(
        savedContent: ContentListViewModel,
        chats: KnowledgeChatViewModel,
        decks: LearningDecksViewModel,
        narrations: CustomNarrationLibraryViewModel
    ) {
        self.savedContent = savedContent
        self.chats = chats
        self.decks = decks
        self.narrations = narrations
        refreshTimelineProjection()
        observeTimelineSources()
    }

    var isLoading: Bool {
        chats.isLoading || savedContent.isLoading || decks.isLoading || narrations.isLoading
    }

    var isLoadingMore: Bool {
        chats.isLoadingMore || savedContent.isLoadingMore
    }

    var hasPaginationError: Bool {
        chats.hasLoadMoreError || savedContent.loadMoreErrorMessage != nil
    }

    var failures: [KnowledgeTimelineFailure] {
        var result: [KnowledgeTimelineFailure] = []
        if savedContent.initialLoadErrorMessage != nil {
            result.append(.savedLoad)
        }
        if let message = savedContent.actionErrorMessage {
            result.append(.savedAction(message))
        }
        if chats.loadErrorMessage != nil {
            result.append(.chatsLoad)
        }
        if let message = chats.errorMessage {
            result.append(.chatsAction(message))
        }
        if decks.loadErrorMessage != nil {
            result.append(.decksLoad)
        }
        if let message = decks.errorMessage {
            result.append(.decksAction(message))
        }
        if narrations.loadErrorMessage != nil {
            result.append(.narrationsLoad)
        }
        if let message = narrations.errorMessage {
            result.append(.narrationsAction(message))
        }
        return result
    }

    func load() async {
        async let savedLoad: Void = savedContent.loadKnowledgeLibrary()
        async let chatLoad: Void = chats.loadChats()
        async let narrationLoad: Void = narrations.load()
        async let deckLoad: Void = decks.load()
        _ = await (savedLoad, chatLoad, narrationLoad, deckLoad)
        refreshTimelineProjection()
    }

    func loadNextPage() async {
        let savedOldest = savedContent.hasMoreContent
            ? savedContent.contents.last?.knowledgeActivityDate
            : nil
        let chatOldest = chats.hasMoreSessions
            ? chats.sessions.last.map { $0.lastActivityDate ?? $0.createdAt }
            : nil
        switch KnowledgePaginationSource.next(
            savedOldest: savedOldest,
            chatOldest: chatOldest
        ) {
        case .saved:
            await savedContent.loadMoreContent()
        case .chats:
            await chats.loadMoreSessions()
        case nil:
            break
        }
        refreshTimelineProjection()
    }

    func recover(_ failure: KnowledgeTimelineFailure) async {
        switch failure {
        case .savedLoad:
            await savedContent.loadKnowledgeLibrary()
        case .savedAction:
            savedContent.clearActionError()
        case .chatsLoad:
            await chats.loadChats()
        case .chatsAction:
            chats.clearError()
        case .decksLoad:
            await decks.load()
        case .decksAction:
            decks.clearError()
        case .narrationsLoad:
            await narrations.load()
        case .narrationsAction:
            narrations.clearError()
        }
        refreshTimelineProjection()
    }

    func cancelTransientWork() {
        chats.cancelVoiceRecording()
        narrations.cancelPolling()
    }

    private func observeTimelineSources() {
        withObservationTracking {
            _ = savedContent.contents
            _ = chats.sessions
            _ = decks.decks
            _ = narrations.episodes
        } onChange: { [weak self] in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.refreshTimelineProjection()
                self.observeTimelineSources()
            }
        }
    }

    private func refreshTimelineProjection() {
        timeline = KnowledgeTimelineItem.merged(
            saved: savedContent.contents,
            chats: chats.sessions,
            decks: decks.decks,
            narrations: narrations.episodes
        )
        groupedTimeline = KnowledgeTimelineDayGroup.group(timeline)
    }
}
