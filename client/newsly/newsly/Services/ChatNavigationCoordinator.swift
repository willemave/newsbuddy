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
    private var navigationReplacementRoutes: Set<ChatSessionRoute> = []
    private(set) var presentedRoute: ChatSessionRoute?

    var queuedRoute: ChatSessionRoute? {
        pendingRoutes.first
    }

    var pendingRoute: ChatSessionRoute? {
        guard presentedRoute == nil else { return nil }
        return queuedRoute
    }

    var queuedRouteReplacesCurrentNavigation: Bool {
        guard let queuedRoute else { return false }
        return navigationReplacementRoutes.contains(queuedRoute)
    }

    private init() {}

    func open(_ route: ChatSessionRoute) {
        guard presentedRoute != route, !pendingRoutes.contains(route) else { return }
        pendingRoutes.append(route)
    }

    func openReplacingCurrentNavigation(_ route: ChatSessionRoute) {
        guard presentedRoute != route, !pendingRoutes.contains(route) else { return }
        navigationReplacementRoutes.insert(route)
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
        navigationReplacementRoutes.remove(route)
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
            navigationReplacementRoutes.removeAll()
            presentedRoute = nil
            return
        }

        pendingRoutes.removeAll { $0 == route }
        navigationReplacementRoutes.remove(route)
        if presentedRoute == route {
            presentedRoute = nil
        }
    }
}
