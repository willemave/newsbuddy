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
    let scrollToTopRequest: Int
    @Namespace private var contentTransitionNamespace
    let viewModel: BriefingViewModel
    let readStateCache: ReadStateCache
    let contentTextSize: DynamicTypeSize
    let dependencyFactory: RootDependencyFactory
    var onSourceMutation: () -> Void = {}

    var body: some View {
        NavigationStack(path: $path) {
            BriefingView(
                viewModel: viewModel,
                playbackService: dependencyFactory.narrationPlaybackService,
                scrollToTopRequest: scrollToTopRequest,
                contentTextSize: contentTextSize,
                onOpenContent: pushDetail,
                onSourceMutation: onSourceMutation
            )
                .withContentRoutes(
                    path: $path,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize,
                    contentTransitionNamespace: contentTransitionNamespace,
                    dependencyFactory: dependencyFactory
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
    let scrollToTopRequest: Int
    let isSelectedRootTab: Bool
    @Namespace private var chatTransitionNamespace
    let viewModel: KnowledgeTimelineViewModel
    let readStateCache: ReadStateCache
    let contentTextSize: DynamicTypeSize
    let dependencyFactory: RootDependencyFactory
    let onOpenSettings: () -> Void
    let onSelectSession: (ChatSessionRoute) -> Void

    var body: some View {
        NavigationStack(path: $path) {
            KnowledgeView(
                scrollToTopRequest: scrollToTopRequest,
                isVisible: isSelectedRootTab && path.isEmpty,
                onSelectContent: pushContent,
                onSelectSession: onSelectSession,
                onSearch: { path.append(KnowledgeSearchRoute()) },
                onOpenSettings: onOpenSettings,
                viewModel: viewModel,
                contentTextSize: contentTextSize,
                chatTransitionNamespace: chatTransitionNamespace
            )
            .navigationDestination(for: KnowledgeSearchRoute.self) { _ in
                KnowledgeSearchView(
                    onSelectContent: pushContent,
                    viewModel: dependencyFactory.makeContentListViewModel(
                        readStateCache: readStateCache
                    )
                )
            }
            .withContentRoutes(
                path: $path,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize,
                chatTransitionNamespace: chatTransitionNamespace,
                allowsChatHistory: true,
                dependencyFactory: dependencyFactory
            )
        }
        .toolbar(.hidden, for: .tabBar)
    }

    private func pushContent(_ route: ContentDetailRoute) {
        path.append(route)
    }
}

struct RootCompactTabBarInset: View {
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
            .background { bottomEdgeFade }
        }
    }

    /// A short, faint fade hugging the bottom screen edge so text settles into
    /// the home-indicator area; everything above it stays readable through the
    /// glass bar.
    private var bottomEdgeFade: some View {
        LinearGradient(
            colors: [Color.surfacePrimary.opacity(0), Color.surfacePrimary.opacity(0.75)],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(height: 24)
        .frame(maxHeight: .infinity, alignment: .bottom)
        .ignoresSafeArea(edges: .bottom)
        .allowsHitTesting(false)
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
    ]
}
