//
//  ContentRoutes.swift
//  newsly
//

import SwiftUI

extension View {
    func withContentRoutes(
        tab: RootTab,
        path: Binding<NavigationPath>,
        readingStateStore: ReadingStateStore,
        readStateCache: ReadStateCache,
        contentTextSize: DynamicTypeSize,
        contentTransitionNamespace: Namespace.ID? = nil,
        chatTransitionNamespace: Namespace.ID? = nil,
        persistentBottomBarHeight: CGFloat = 0
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
                persistentBottomBarHeight: persistentBottomBarHeight,
                onShowHistory: tab == .knowledge
                    ? {
                        path.wrappedValue = NavigationPath()
                        path.wrappedValue.append(SessionHistoryRoute())
                    }
                    : nil
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
        .navigationDestination(for: KnowledgeLibraryRoute.self) { _ in
            KnowledgeLibraryView(readStateCache: readStateCache)
        }
    }
}
