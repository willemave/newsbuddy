//
//  ChatNavigationCoordinator.swift
//  newsly
//
//  Created by Assistant on 4/6/26.
//

import Foundation
import Observation

@MainActor
@Observable
final class ChatNavigationCoordinator {
    static let shared = ChatNavigationCoordinator()

    /// App-level sink for chat entry routes originating outside the current
    /// navigation stack (notifications, content actions, quick actions, etc.).
    private(set) var pendingRoute: ChatSessionRoute?

    private init() {}

    func open(_ route: ChatSessionRoute) {
        pendingRoute = route
    }

    func openAssistantTurn(_ response: AssistantTurnResponse) {
        open(ChatSessionRoute(assistantTurn: response))
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
