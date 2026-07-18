//
//  RootTabs.swift
//  newsly
//

import os.log
import SwiftUI

private let rootTabLogger = Logger(subsystem: "com.newsly", category: "ContentView")

struct LongFormTab: View {
    @Binding var path: NavigationPath
    @Namespace private var contentTransitionNamespace
    let viewModel: LongContentListViewModel
    let isActive: Bool
    let badge: Int
    let readingStateStore: ReadingStateStore
    let readStateCache: ReadStateCache
    let contentTextSize: DynamicTypeSize
    let scrollToTopRequest: Int
    let onShowNarrations: () -> Void
    let currentFastReadItems: () -> [ContentSummary]

    var body: some View {
        NavigationStack(path: $path) {
            LongFormView(
                viewModel: viewModel,
                isActive: isActive,
                onSelect: pushDetail,
                scrollToTopRequest: scrollToTopRequest,
                contentTransitionNamespace: contentTransitionNamespace,
                onShowNarrations: onShowNarrations,
                currentFastReadItems: currentFastReadItems
            )
            .dynamicTypeSize(contentTextSize)
            .withContentRoutes(
                tab: .longContent,
                path: $path,
                readingStateStore: readingStateStore,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize,
                contentTransitionNamespace: contentTransitionNamespace
            )
        }
        .toolbar(path.isEmpty ? .visible : .hidden, for: .tabBar)
        .tag(RootTab.longContent)
        .tabItem {
            Label("Long", systemImage: "doc.richtext")
                .accessibilityIdentifier("tab.long_form")
        }
        .badge(badge)
    }

    private func pushDetail(_ route: ContentDetailRoute) {
        guard path.isEmpty else {
            rootTabLogger.info(
                "[Navigation] ignoredDuplicatePush tab=long_form contentId=\(route.contentId, privacy: .public) activePathCount=\(path.count, privacy: .public)"
            )
            return
        }

        rootTabLogger.info(
            "[Navigation] pushDetail tab=long_form contentId=\(route.contentId, privacy: .public) type=\(route.contentType.rawValue, privacy: .public) idsCount=\(route.allContentIds.count, privacy: .public) pathCountBefore=\(path.count, privacy: .public)"
        )
        path.append(route)
    }
}

struct ShortFormTab: View {
    @Binding var path: NavigationPath
    let viewModel: ShortNewsListViewModel
    let isActive: Bool
    let badge: Int
    let readingStateStore: ReadingStateStore
    let readStateCache: ReadStateCache
    let contentTextSize: DynamicTypeSize
    let scrollToTopRequest: Int

    var body: some View {
        NavigationStack(path: $path) {
            ShortFormView(
                viewModel: viewModel,
                isActive: isActive,
                onSelect: pushDetail,
                scrollToTopRequest: scrollToTopRequest
            )
            .dynamicTypeSize(contentTextSize)
            .withContentRoutes(
                tab: .shortNews,
                path: $path,
                readingStateStore: readingStateStore,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize
            )
        }
        .toolbar(path.isEmpty ? .visible : .hidden, for: .tabBar)
        .tag(RootTab.shortNews)
        .tabItem {
            Label("Fast", systemImage: "bolt.fill")
                .accessibilityIdentifier("tab.fast_news")
        }
        .badge(badge)
    }

    private func pushDetail(_ route: ContentDetailRoute) {
        guard path.isEmpty else {
            rootTabLogger.info(
                "[Navigation] ignoredDuplicatePush tab=fast_news contentId=\(route.contentId, privacy: .public) activePathCount=\(path.count, privacy: .public)"
            )
            return
        }

        rootTabLogger.info(
            "[Navigation] pushDetail tab=fast_news contentId=\(route.contentId, privacy: .public) type=\(route.contentType.rawValue, privacy: .public) idsCount=\(route.allContentIds.count, privacy: .public) pathCountBefore=\(path.count, privacy: .public)"
        )
        path.append(route)
    }
}

struct BriefingTab: View {
    @Binding var path: NavigationPath
    let viewModel: BriefingViewModel
    let contentTextSize: DynamicTypeSize

    var body: some View {
        NavigationStack(path: $path) {
            BriefingView(viewModel: viewModel)
                .dynamicTypeSize(contentTextSize)
        }
        .toolbar(.hidden, for: .tabBar)
        .tag(RootTab.briefing)
        .tabItem {
            Label("Briefing", systemImage: "newspaper")
                .accessibilityIdentifier("tab.briefing")
        }
    }
}

struct KnowledgeTab: View {
    @Binding var path: NavigationPath
    let isBriefingExperience: Bool
    let readStateCache: ReadStateCache
    let readingStateStore: ReadingStateStore
    let contentTextSize: DynamicTypeSize
    let onOpenMore: () -> Void

    var body: some View {
        NavigationStack(path: $path) {
            KnowledgeView(
                onSelectContent: pushContent,
                onSearch: { path.append(KnowledgeSearchRoute()) },
                onOpenMore: isBriefingExperience ? onOpenMore : nil,
                readStateCache: readStateCache
            )
            .navigationDestination(for: KnowledgeSearchRoute.self) { _ in
                KnowledgeSearchView(
                    onSelectContent: pushContent,
                    readStateCache: readStateCache
                )
            }
            .withContentRoutes(
                tab: .knowledge,
                path: $path,
                readingStateStore: readingStateStore,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize
            )
        }
        .toolbar(isBriefingExperience ? .hidden : .visible, for: .tabBar)
        .tag(RootTab.knowledge)
        .tabItem {
            Label("Knowledge", systemImage: "books.vertical.fill")
                .accessibilityIdentifier("tab.knowledge")
        }
    }

    private func pushContent(_ route: ContentDetailRoute) {
        path.append(route)
    }
}

struct LearningTab: View {
    @Binding var path: NavigationPath
    @Binding var focusRequest: LearningFocusRequest?
    @Namespace private var chatTransitionNamespace
    let isBriefingExperience: Bool
    let viewModel: LearningHubViewModel
    let readStateCache: ReadStateCache
    let readingStateStore: ReadingStateStore
    let contentTextSize: DynamicTypeSize
    let onOpenMore: () -> Void

    var body: some View {
        NavigationStack(path: $path) {
            LearningView(
                focusRequest: focusRequest,
                onFocusHandled: clearFocusRequest,
                onSelectSession: pushSession,
                onOpenMore: isBriefingExperience ? onOpenMore : nil,
                viewModel: viewModel,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize,
                chatTransitionNamespace: chatTransitionNamespace
            )
            .withContentRoutes(
                tab: .learning,
                path: $path,
                readingStateStore: readingStateStore,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize,
                chatTransitionNamespace: chatTransitionNamespace
            )
        }
        .toolbar(isBriefingExperience ? .hidden : .visible, for: .tabBar)
        .tag(RootTab.learning)
        .tabItem {
            Label("Learning", systemImage: "sparkles")
                .accessibilityIdentifier("tab.learning")
        }
    }

    private func clearFocusRequest(_ request: LearningFocusRequest) {
        if focusRequest == request { focusRequest = nil }
    }

    private func pushSession(_ route: ChatSessionRoute) {
        path = NavigationPath()
        path.append(route)
    }
}

struct MoreTab: View {
    @Binding var path: NavigationPath
    let submissionsViewModel: SubmissionStatusViewModel
    let readStateCache: ReadStateCache
    let readingStateStore: ReadingStateStore
    let contentTextSize: DynamicTypeSize
    let badge: Int

    var body: some View {
        NavigationStack(path: $path) {
            MoreView(
                submissionsViewModel: submissionsViewModel,
                readStateCache: readStateCache
            )
            .withContentRoutes(
                tab: .more,
                path: $path,
                readingStateStore: readingStateStore,
                readStateCache: readStateCache,
                contentTextSize: contentTextSize
            )
        }
        .tag(RootTab.more)
        .tabItem {
            Label("More", systemImage: "ellipsis.circle.fill")
                .accessibilityIdentifier("tab.more")
        }
        .badge(badge)
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
