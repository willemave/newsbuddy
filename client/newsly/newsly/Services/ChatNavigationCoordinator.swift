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
    /// App-level sink for global chat routes originating outside the current
    /// navigation stack (notifications, Knowledge entry points). Chats started
    /// from content attach to it instead (`AttachedChatController`).
    private var pendingRoutes: [ChatSessionRoute] = []
    private(set) var presentedRoute: ChatSessionRoute?

    var queuedRoute: ChatSessionRoute? {
        pendingRoutes.first
    }

    init() {}

    func open(_ route: ChatSessionRoute) {
        guard !matchesIntent(presentedRoute, route),
              !pendingRoutes.contains(where: { $0.stableKey == route.stableKey }) else {
            return
        }
        pendingRoutes.append(route)
    }

    @discardableResult
    func beginPresentation(
        _ route: ChatSessionRoute,
        replacingPresented: Bool = false
    ) -> Bool {
        guard (presentedRoute == nil || replacingPresented), matchesIntent(queuedRoute, route) else {
            return false
        }
        pendingRoutes.removeFirst()
        presentedRoute = route
        return true
    }

    @discardableResult
    func acknowledgePresented(_ route: ChatSessionRoute) -> Bool {
        guard matchesIntent(presentedRoute, route) else { return false }
        presentedRoute = nil
        return true
    }

    func clear(route: ChatSessionRoute? = nil) {
        guard let route else {
            pendingRoutes.removeAll()
            presentedRoute = nil
            return
        }

        pendingRoutes.removeAll { $0.stableKey == route.stableKey }
        if matchesIntent(presentedRoute, route) {
            presentedRoute = nil
        }
    }

    private func matchesIntent(_ existing: ChatSessionRoute?, _ candidate: ChatSessionRoute) -> Bool {
        existing?.stableKey == candidate.stableKey
    }
}
