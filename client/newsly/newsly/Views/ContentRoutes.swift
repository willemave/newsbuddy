//
//  ContentRoutes.swift
//  newsly
//

import SwiftUI

extension View {
    /// The content detail destination alone, for stacks whose path holds only
    /// `ContentDetailRoute` (the typed Briefing path).
    func withContentDetailRoute(
        readStateCache: ReadStateCache,
        contentTextSize: DynamicTypeSize,
        contentTransitionNamespace: Namespace.ID? = nil
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
            .environment(readStateCache)
            .contentZoomNavigationTransition(id: route.contentId, namespace: contentTransitionNamespace)
        }
    }

    func withContentRoutes(
        path: Binding<NavigationPath>,
        readStateCache: ReadStateCache,
        contentTextSize: DynamicTypeSize,
        contentTransitionNamespace: Namespace.ID? = nil,
        chatTransitionNamespace: Namespace.ID? = nil,
        allowsChatHistory: Bool = false,
        dependencyFactory: RootDependencyFactory
    ) -> some View {
        withContentDetailRoute(
            readStateCache: readStateCache,
            contentTextSize: contentTextSize,
            contentTransitionNamespace: contentTransitionNamespace
        )
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
                viewModel: dependencyFactory.makeChatSessionsViewModel(),
                chatTransitionNamespace: chatTransitionNamespace
            )
            .dynamicTypeSize(contentTextSize)
        }
    }
}
