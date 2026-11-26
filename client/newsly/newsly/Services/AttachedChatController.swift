//
//  AttachedChatController.swift
//  newsly
//

import Foundation
import Observation

/// Where an attached chat is presented. Each host is a view that can show the
/// chat over its own content: the app root (Briefing, Knowledge), the Settings
/// sheet, or the full-screen reader.
enum AttachedChatHost: Hashable, Sendable {
    case root
    case settings
    case reader
}

/// Owns the one chat that a reader opened from content (detail, Briefing card,
/// reader selection, summary topic). The chat stays attached to its source
/// rather than replacing navigation, and its view model lives here, above the
/// views that host it, so moving between hosts or between the compact and
/// open-display layouts keeps the draft, dictation, and in-flight turn.
@MainActor
@Observable
final class AttachedChatController {
    struct Presentation: Identifiable, Equatable {
        let id: UUID
        let route: ChatSessionRoute
        var host: AttachedChatHost
    }

    private(set) var presentation: Presentation?

    @ObservationIgnored private var viewModel: ChatSessionViewModel?
    @ObservationIgnored private var viewModelPresentationID: UUID?
    @ObservationIgnored private var feedOptionActionModel: AssistantFeedOptionActionModel?

    init() {}

    /// Presents `route` attached to `host`. Re-presenting the same session
    /// (for example from its banner) only moves it and keeps the draft. A
    /// route that carries a new turn, or another session, replaces the chat.
    func present(_ route: ChatSessionRoute, host: AttachedChatHost) {
        if var current = presentation, Self.continues(current.route, with: route) {
            guard current.host != host else { return }
            current.host = host
            presentation = current
            return
        }
        releaseViewModel()
        presentation = Presentation(id: UUID(), route: route, host: host)
    }

    /// Moves a chat shown in `source` to `destination`, for example when the
    /// reader that hosted it closes.
    func move(from source: AttachedChatHost, to destination: AttachedChatHost) {
        guard var current = presentation, current.host == source else { return }
        current.host = destination
        presentation = current
    }

    /// Ends the attached chat. An unfinished reply hands off to background
    /// tracking, so the source's chat banner reports its completion.
    func close() {
        releaseViewModel()
        presentation = nil
    }


    /// The presentation's view model, created by the first host that renders
    /// it. Not observed: hosts re-render from `presentation`, never from this.
    func viewModel(
        for presentation: Presentation,
        make: () -> ChatSessionViewModel
    ) -> ChatSessionViewModel {
        if let viewModel, viewModelPresentationID == presentation.id {
            return viewModel
        }
        let created = make()
        // Only the current presentation's model is kept; a stale host that
        // renders after a replace gets a throwaway model.
        if self.presentation?.id == presentation.id {
            viewModel = created
            viewModelPresentationID = presentation.id
        }
        return created
    }

    /// Same session and no new turn to show: keep the live view model.
    static func continues(_ current: ChatSessionRoute, with next: ChatSessionRoute) -> Bool {
        if current == next { return true }
        guard current.sessionId == next.sessionId else { return false }
        return next.pendingMessageId == nil && next.initialUserMessageText == nil
    }

    func feedOptionActionModel(
        make: () -> AssistantFeedOptionActionModel
    ) -> AssistantFeedOptionActionModel {
        if let feedOptionActionModel {
            return feedOptionActionModel
        }
        let created = make()
        feedOptionActionModel = created
        return created
    }

    private func releaseViewModel() {
        viewModel?.handleDisappear()
        viewModel = nil
        viewModelPresentationID = nil
        feedOptionActionModel = nil
    }
}
