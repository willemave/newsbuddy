//
//  ContentRoutes.swift
//  newsly
//

import SwiftUI

extension View {
    func withContentRoutes(
        path: Binding<NavigationPath>,
        readingStateStore: ReadingStateStore,
        readStateCache: ReadStateCache,
        contentTextSize: DynamicTypeSize,
        contentTransitionNamespace: Namespace.ID? = nil,
        chatTransitionNamespace: Namespace.ID? = nil,
        allowsChatHistory: Bool = false
    ) -> some View {
        navigationDestination(for: ContentDetailRoute.self) { route in
            ContentDetailView(
                contentId: route.contentId,
                contentType: route.contentType,
                allContentIds: route.allContentIds,
                navigationSurface: route.navigationSurface,
                initialScrollTarget: route.initialScrollTarget,
                readStateCache: readStateCache
            )
            .dynamicTypeSize(contentTextSize)
            .environment(readingStateStore)
            .environment(readStateCache)
            .contentZoomNavigationTransition(id: route.contentId, namespace: contentTransitionNamespace)
        }
        .navigationDestination(for: ChatSessionRoute.self) { route in
            ChatSessionView(
                route: route,
                onShowHistory: allowsChatHistory
                    ? {
                        path.wrappedValue = NavigationPath()
                        path.wrappedValue.append(SessionHistoryRoute())
                    }
                    : nil,
                onClose: {
                    guard !path.wrappedValue.isEmpty else { return }
                    path.wrappedValue.removeLast()
                }
            )
            .id(route.stableKey)
            .dynamicTypeSize(contentTextSize)
            .contentZoomNavigationTransition(id: route.sessionId, namespace: chatTransitionNamespace)
        }
        .navigationDestination(for: SessionHistoryRoute.self) { _ in
            ChatSessionHistoryView(
                onSelectSession: { route in
                    path.wrappedValue.append(route)
                },
                chatTransitionNamespace: chatTransitionNamespace
            )
            .dynamicTypeSize(contentTextSize)
        }
    }
}
