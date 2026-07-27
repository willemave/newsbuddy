//
//  RootTabs.swift
//  newsly
//

import os.log
import SwiftUI

private let rootTabLogger = Logger(subsystem: "com.newsly", category: "ContentView")

private func pushBriefingContentDetail(
    _ route: ContentDetailRoute,
    path: Binding<NavigationPath>
) {
    guard path.wrappedValue.isEmpty else {
        rootTabLogger.info(
            "[Navigation] ignoredDuplicatePush tab=briefing contentId=\(route.contentId, privacy: .public) activePathCount=\(path.wrappedValue.count, privacy: .public)"
        )
        return
    }

    rootTabLogger.info(
        "[Navigation] pushDetail tab=briefing contentId=\(route.contentId, privacy: .public) type=\(route.contentType.rawValue, privacy: .public) idsCount=\(route.allContentIds.count, privacy: .public) pathCountBefore=\(path.wrappedValue.count, privacy: .public)"
    )
    path.wrappedValue.append(route)
}

struct BriefingTab: View {
    @Binding var path: NavigationPath
    @Namespace private var contentTransitionNamespace
    let viewModel: BriefingViewModel
    let readingStateStore: ReadingStateStore
    let readStateCache: ReadStateCache
    let contentTextSize: DynamicTypeSize

    var body: some View {
        NavigationStack(path: $path) {
            BriefingView(viewModel: viewModel, onOpenContent: pushDetail)
                .dynamicTypeSize(contentTextSize)
                .withContentRoutes(
                    path: $path,
                    readingStateStore: readingStateStore,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize,
                    contentTransitionNamespace: contentTransitionNamespace
                )
        }
        .toolbar(.hidden, for: .tabBar)
    }

    private func pushDetail(_ route: ContentDetailRoute) {
        pushBriefingContentDetail(route, path: $path)
    }
}

struct KnowledgeTab: View {
    @Binding var path: NavigationPath
    let readStateCache: ReadStateCache
    let readingStateStore: ReadingStateStore
    let contentTextSize: DynamicTypeSize
    let onOpenMore: () -> Void

    var body: some View {
        NavigationStack(path: $path) {
            KnowledgeView(
                onSelectContent: pushContent,
                onSearch: { path.append(KnowledgeSearchRoute()) },
                onOpenMore: onOpenMore,
                readStateCache: readStateCache
            )
            .navigationDestination(for: KnowledgeSearchRoute.self) { _ in
                KnowledgeSearchView(
                    onSelectContent: pushContent,
                    readStateCache: readStateCache
                )
            }
            .withContentRoutes(
                path: $path,
                readingStateStore: readingStateStore,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize
            )
        }
        .toolbar(.hidden, for: .tabBar)
    }

    private func pushContent(_ route: ContentDetailRoute) {
        path.append(route)
    }
}

struct LearningTab: View {
    @Binding var path: NavigationPath
    @Namespace private var chatTransitionNamespace
    let viewModel: LearningHubViewModel
    let readStateCache: ReadStateCache
    let readingStateStore: ReadingStateStore
    let contentTextSize: DynamicTypeSize
    let onOpenMore: () -> Void

    var body: some View {
        NavigationStack(path: $path) {
            LearningView(
                onSelectSession: pushSession,
                onOpenMore: onOpenMore,
                viewModel: viewModel,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize,
                chatTransitionNamespace: chatTransitionNamespace
            )
            .withContentRoutes(
                path: $path,
                readingStateStore: readingStateStore,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize,
                chatTransitionNamespace: chatTransitionNamespace,
                allowsChatHistory: true
            )
        }
        .toolbar(.hidden, for: .tabBar)
    }

    private func pushSession(_ route: ChatSessionRoute) {
        path = NavigationPath()
        path.append(route)
    }
}

struct BriefingCompactTabBarInset: View {
    let selectedTab: RootTab
    let isVisible: Bool
    let onSelect: (RootTab) -> Void

    var body: some View {
        if isVisible {
            CompactTabBar(
                items: Self.items,
                selection: selectedTab,
                onSelect: onSelect
            )
        }
    }

    private static let items: [CompactTabBar.Item] = [
        CompactTabBar.Item(
            tab: .briefing,
            label: "Briefing",
            icon: "newspaper",
            accessibilityIdentifier: "tab.briefing"
        ),
        CompactTabBar.Item(
            tab: .knowledge,
            label: "Knowledge",
            icon: "books.vertical.fill",
            accessibilityIdentifier: "tab.knowledge"
        ),
        CompactTabBar.Item(
            tab: .learning,
            label: "Learning",
            icon: "sparkles",
            accessibilityIdentifier: "tab.learning"
        )
    ]
}
