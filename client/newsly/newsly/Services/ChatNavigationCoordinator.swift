//
//  ChatNavigationCoordinator.swift
//  newsly
//
//  Created by Assistant on 4/6/26.
//

import Foundation

@MainActor
final class ChatNavigationCoordinator: ObservableObject {
    static let shared = ChatNavigationCoordinator()

    /// App-level sink for chat entry routes originating outside the current
    /// navigation stack (notifications, content actions, quick actions, etc.).
    @Published private(set) var pendingRoute: ChatSessionRoute?

    private init() {}

    func open(_ route: ChatSessionRoute) {
        pendingRoute = route
    }

    func openAssistantTurn(_ response: AssistantTurnResponse) {
        open(
            ChatSessionRoute(
                session: response.session,
                initialUserMessageText: response.userMessage.content,
                initialUserMessageTimestamp: response.userMessage.timestamp,
                pendingMessageId: response.messageId
            )
        )
    }

    func clear(route: ChatSessionRoute? = nil) {
        guard let route else {
            pendingRoute = nil
            return
        }

        if pendingRoute == route {
            pendingRoute = nil
        }
    }
}

enum FeedDigDeeperSurface {
    case shortNews
    case longForm

    var screenType: String {
        switch self {
        case .shortNews:
            "short_news_feed"
        case .longForm:
            "long_form_feed"
        }
    }

    var screenTitle: String {
        switch self {
        case .shortNews:
            "Fast read"
        case .longForm:
            "Long read"
        }
    }

    var contextNote: String {
        switch self {
        case .shortNews:
            "The user selected text from a fast-read feed item. Use the selected item and nearby short-form feed context first."
        case .longForm:
            "The user selected text from a long-read card. Use the selected item and nearby long-form feed context first."
        }
    }
}

enum FeedDigDeeperAction {
    static func start(
        selectedText: String,
        item: ContentSummary,
        visibleContentIds: [Int],
        surface: FeedDigDeeperSurface
    ) {
        let trimmed = selectedText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }

        Task { @MainActor in
            do {
                let isNews = item.apiContentType == .news
                let response = try await ChatService.shared.createAssistantTurn(
                    message: "Dig deeper into this selected text from \(item.displayTitle): \"\(trimmed)\"",
                    screenContext: AssistantScreenContext(
                        screenType: surface.screenType,
                        screenTitle: surface.screenTitle,
                        contentId: isNews ? nil : item.id,
                        newsItemId: isNews ? item.id : nil,
                        visibleContentIds: isNews ? [] : visibleContentIds,
                        visibleNewsItemIds: isNews ? visibleContentIds : [],
                        selectedTopic: trimmed,
                        query: trimmed,
                        note: surface.contextNote
                    )
                )
                ChatNavigationCoordinator.shared.openAssistantTurn(response)
            } catch {
                ToastService.shared.showError("Failed to dig deeper: \(error.localizedDescription)")
            }
        }
    }
}
