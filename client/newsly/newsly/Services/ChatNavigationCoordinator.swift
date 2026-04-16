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
