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
    private var pendingRoutes: [ChatSessionRoute] = []
    private(set) var presentedRoute: ChatSessionRoute?

    var queuedRoute: ChatSessionRoute? {
        pendingRoutes.first
    }

    var pendingRoute: ChatSessionRoute? {
        guard presentedRoute == nil else { return nil }
        return queuedRoute
    }

    private init() {}

    func open(_ route: ChatSessionRoute) {
        guard presentedRoute != route, !pendingRoutes.contains(route) else { return }
        pendingRoutes.append(route)
    }

    func openAssistantTurn(_ response: AssistantTurnResponse) {
        open(ChatSessionRoute(assistantTurn: response))
    }

    @discardableResult
    func beginPresentation(
        _ route: ChatSessionRoute,
        replacingPresented: Bool = false
    ) -> Bool {
        guard (presentedRoute == nil || replacingPresented), queuedRoute == route else {
            return false
        }
        pendingRoutes.removeFirst()
        presentedRoute = route
        return true
    }

    @discardableResult
    func acknowledgePresented(_ route: ChatSessionRoute) -> Bool {
        guard presentedRoute == route else { return false }
        presentedRoute = nil
        return true
    }

    func clear(route: ChatSessionRoute? = nil) {
        guard let route else {
            pendingRoutes.removeAll()
            presentedRoute = nil
            return
        }

        pendingRoutes.removeAll { $0 == route }
        if presentedRoute == route {
            presentedRoute = nil
        }
    }
}
