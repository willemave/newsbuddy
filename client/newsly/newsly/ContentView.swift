//
//  ContentView.swift
//  newsly
//
//  Created by Willem Ave on 7/8/25.
//

import os.log
import SwiftUI

private let logger = Logger(subsystem: "com.newsly", category: "ContentView")

struct ContentView: View {
    @StateObject private var unreadCountService = UnreadCountService.shared
    @StateObject private var readingStateStore: ReadingStateStore
    @StateObject private var tabCoordinator: TabCoordinatorViewModel
    @StateObject private var chatSessionManager = ActiveChatSessionManager.shared
    @StateObject private var chatNavigation = ChatNavigationCoordinator.shared
    @StateObject private var submissionStatusViewModel = SubmissionStatusViewModel()
    @ObservedObject private var settings = AppSettings.shared

    @State private var longFormPath = NavigationPath()
    @State private var shortFormPath = NavigationPath()
    @State private var knowledgePath = NavigationPath()
    @State private var isRestoringPath = false
    @State private var hasAppliedE2EOpenChatRoute = false
    @State private var hasAppliedE2EOpenContentRoute = false
    @State private var knowledgeFocusRequest: KnowledgeFocusRequest?
    @Environment(\.scenePhase) private var scenePhase

    @MainActor
    init(userId: Int? = nil, tabCoordinator: TabCoordinatorViewModel? = nil) {
        _readingStateStore = StateObject(wrappedValue: ReadingStateStore(userId: userId))
        _tabCoordinator = StateObject(wrappedValue: tabCoordinator ?? RootDependencyFactory.makeTabCoordinator())
    }

    private var contentTextSize: DynamicTypeSize {
        ContentTextSize(index: settings.contentTextSizeIndex).dynamicTypeSize
    }

    private var longBadge: String? {
        let total = unreadCountService.articleCount + unreadCountService.podcastCount
        return total > 0 ? String(total) : nil
    }

    private var shortBadge: String? {
        let count = unreadCountService.newsCount
        return count > 0 ? String(count) : nil
    }

    private var knowledgeBadge: String? {
        // Show processing indicator if any sessions are being processed
        chatSessionManager.hasProcessingSessions ? "●" : nil
    }

    private var moreBadge: String? {
        let count = submissionStatusViewModel.unseenCount
        return count > 0 ? String(count) : nil
    }

    private var selectedTabBinding: Binding<RootTab> {
        Binding(
            get: { tabCoordinator.selectedTab },
            set: { newTab in
                // TabView writes the current selection again on repeated tab taps.
                // Avoid publishing a no-op change through the whole app shell.
                guard tabCoordinator.selectedTab != newTab else { return }
                tabCoordinator.selectedTab = newTab
            }
        )
    }

    var body: some View {
        TabView(selection: selectedTabBinding) {
            NavigationStack(path: $longFormPath) {
                LongFormView(
                    viewModel: tabCoordinator.longContentVM,
                    isActive: tabCoordinator.selectedTab == .longContent,
                    onSelect: { route in
                        logger.info(
                            "[Navigation] pushDetail tab=long_form contentId=\(route.contentId, privacy: .public) type=\(route.contentType.rawValue, privacy: .public) idsCount=\(route.allContentIds.count, privacy: .public) pathCountBefore=\(longFormPath.count, privacy: .public)"
                        )
                        longFormPath.append(route)
                    },
                    onShowNarrations: {
                        openKnowledgeNarrations()
                    },
                    currentFastReadItems: {
                        tabCoordinator.shortNewsVM.currentItems()
                    }
                )
                .withContentRoutes(
                    tab: .longContent,
                    path: $longFormPath,
                    readingStateStore: readingStateStore,
                    contentTextSize: contentTextSize
                )
                .id(settings.readerPaletteId)
            }
            .toolbar(longFormPath.isEmpty ? .visible : .hidden, for: .tabBar)
            .tag(RootTab.longContent)
            .tabItem {
                Label("Long", systemImage: "doc.richtext")
                    .accessibilityIdentifier("tab.long_form")
            }
            .badge(longBadge != nil ? Int(longBadge!) ?? 0 : 0)

            NavigationStack(path: $shortFormPath) {
                ShortFormView(
                    viewModel: tabCoordinator.shortNewsVM,
                    isActive: tabCoordinator.selectedTab == .shortNews,
                    onSelect: { route in
                        logger.info(
                            "[Navigation] pushDetail tab=fast_news contentId=\(route.contentId, privacy: .public) type=\(route.contentType.rawValue, privacy: .public) idsCount=\(route.allContentIds.count, privacy: .public) pathCountBefore=\(shortFormPath.count, privacy: .public)"
                        )
                        shortFormPath.append(route)
                    }
                )
                .withContentRoutes(
                    tab: .shortNews,
                    path: $shortFormPath,
                    readingStateStore: readingStateStore,
                    contentTextSize: contentTextSize
                )
                .id(settings.readerPaletteId)
            }
            .toolbar(shortFormPath.isEmpty ? .visible : .hidden, for: .tabBar)
            .tag(RootTab.shortNews)
            .tabItem {
                Label("Fast", systemImage: "bolt.fill")
                    .accessibilityIdentifier("tab.fast_news")
            }
            .badge(shortBadge != nil ? Int(shortBadge!) ?? 0 : 0)

            NavigationStack(path: $knowledgePath) {
                KnowledgeView(
                    focusRequest: knowledgeFocusRequest,
                    onFocusHandled: { request in
                        if knowledgeFocusRequest == request {
                            knowledgeFocusRequest = nil
                        }
                    },
                    onSelectSession: { route in
                        knowledgePath = NavigationPath()
                        knowledgePath.append(route)
                    },
                    onShowKnowledgeLibrary: {
                        knowledgePath.append(KnowledgeLibraryRoute())
                    }
                )
                .withContentRoutes(
                    tab: .knowledge,
                    path: $knowledgePath,
                    readingStateStore: readingStateStore,
                    contentTextSize: contentTextSize
                )
                .id(settings.readerPaletteId)
            }
            .tag(RootTab.knowledge)
            .tabItem {
                Label("Knowledge", systemImage: "books.vertical.fill")
                    .accessibilityIdentifier("tab.knowledge")
            }

            NavigationStack {
                MoreView(submissionsViewModel: submissionStatusViewModel)
            }
            .tag(RootTab.more)
            .tabItem {
                Label("More", systemImage: "ellipsis.circle.fill")
                    .accessibilityIdentifier("tab.more")
            }
            .badge(moreBadge != nil ? Int(moreBadge!) ?? 0 : 0)
        }
        .tint(Color.appChromeAccent)
        .font(.appBody)
        .dynamicTypeSize(AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize)
        .environmentObject(readingStateStore)
        .onAppear {
            AppChrome.configure()
            tabCoordinator.ensureInitialLoads()
            restoreIfNeeded()
            applyE2EOpenContentRouteIfNeeded()
            applyE2EOpenChatRouteIfNeeded()
        }
        .onChange(of: settings.readerPaletteId) { _, _ in
            AppChrome.configure()
        }
        .onChange(of: tabCoordinator.selectedTab) { _, newValue in
            logger.info("[TabChange] selectedTab=\(String(describing: newValue), privacy: .public)")
            tabCoordinator.handleTabChange(to: newValue)
        }
        .onChange(of: longFormPath.count) { oldValue, newValue in
            logger.info(
                "[Navigation] pathChanged tab=long_form oldCount=\(oldValue, privacy: .public) newCount=\(newValue, privacy: .public)"
            )
        }
        .onChange(of: shortFormPath.count) { oldValue, newValue in
            logger.info(
                "[Navigation] pathChanged tab=fast_news oldCount=\(oldValue, privacy: .public) newCount=\(newValue, privacy: .public)"
            )
        }
        .onChange(of: knowledgePath.count) { oldValue, newValue in
            logger.info(
                "[Navigation] pathChanged tab=knowledge oldCount=\(oldValue, privacy: .public) newCount=\(newValue, privacy: .public)"
            )
        }
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase == .active {
                restoreIfNeeded()
                applyE2EOpenContentRouteIfNeeded()
                applyE2EOpenChatRouteIfNeeded()
            }
        }
        .onReceive(chatNavigation.$pendingRoute) { route in
            guard let route else { return }
            logger.info("[Navigation] openChatSession sessionId=\(route.sessionId, privacy: .public)")
            openChatSession(route: route)
            chatNavigation.clear(route: route)
        }
        .task {
            await unreadCountService.refreshCounts()
            await submissionStatusViewModel.load()
        }
    }

    private func restoreIfNeeded() {
        let isNews = readingStateStore.current?.contentType == .news
        let targetPath = isNews ? shortFormPath : longFormPath
        guard !isRestoringPath, targetPath.isEmpty, let state = readingStateStore.current else { return }

        isRestoringPath = true
        logger.info(
            "[NavigationRestore] contentId=\(state.contentId, privacy: .public) contentType=\(state.contentType.rawValue, privacy: .public)"
        )
        let targetTab: RootTab = isNews ? .shortNews : .longContent
        if tabCoordinator.selectedTab != targetTab {
            tabCoordinator.selectedTab = targetTab
        }

        Task { @MainActor in
            await Task.yield()
            defer { isRestoringPath = false }

            let currentIds: [Int]
            if isNews {
                guard shortFormPath.isEmpty else { return }
                let ids = tabCoordinator.shortNewsVM.currentItems().map(\.id)
                currentIds = ids.isEmpty ? [state.contentId] : ids
            } else {
                guard longFormPath.isEmpty else { return }
                let ids = tabCoordinator.longContentVM.currentItems().map(\.id)
                currentIds = ids.isEmpty ? [state.contentId] : ids
            }

            let route = ContentDetailRoute(
                contentId: state.contentId,
                contentType: state.contentType,
                allContentIds: currentIds,
                navigationSurface: isNews ? .fastNews : .longForm
            )

            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                if isNews {
                    shortFormPath.append(route)
                } else {
                    longFormPath.append(route)
                }
            }
            logger.info("[NavigationRestore] pathRestored idsCount=\(currentIds.count, privacy: .public)")
        }
    }

    private func openChatSession(route: ChatSessionRoute) {
        tabCoordinator.selectedTab = .knowledge
        knowledgePath = NavigationPath()
        knowledgePath.append(route)
    }

    private func openKnowledgeNarrations() {
        knowledgePath = NavigationPath()
        knowledgeFocusRequest = KnowledgeFocusRequest(target: .narrations)
        tabCoordinator.selectedTab = .knowledge
    }

    private func applyE2EOpenChatRouteIfNeeded() {
        guard !hasAppliedE2EOpenChatRoute else { return }
        guard let sessionId = E2ETestLaunch.openChatSessionId else { return }

        hasAppliedE2EOpenChatRoute = true
        Task { @MainActor in
            await Task.yield()
            openChatSession(route: ChatSessionRoute(sessionId: sessionId))
        }
    }

    private func applyE2EOpenContentRouteIfNeeded() {
        guard !hasAppliedE2EOpenContentRoute else { return }
        guard let contentId = E2ETestLaunch.openContentId else { return }

        hasAppliedE2EOpenContentRoute = true
        let rawType = E2ETestLaunch.openContentType ?? ContentType.news.rawValue
        let contentType = ContentType(rawValue: rawType) ?? .news
        let route = ContentDetailRoute(
            contentId: contentId,
            contentType: contentType,
            allContentIds: [contentId],
            navigationSurface: contentType == .news ? .fastNews : .longForm
        )

        Task { @MainActor in
            await Task.yield()
            switch contentType {
            case .news:
                tabCoordinator.selectedTab = .shortNews
                shortFormPath = NavigationPath()
                shortFormPath.append(route)
            case .article, .podcast:
                tabCoordinator.selectedTab = .longContent
                longFormPath = NavigationPath()
                longFormPath.append(route)
            }
        }
    }
}

// MARK: - Content navigation destinations

private extension View {
    func withContentRoutes(
        tab: RootTab,
        path: Binding<NavigationPath>,
        readingStateStore: ReadingStateStore,
        contentTextSize: DynamicTypeSize
    ) -> some View {
        self
            .navigationDestination(for: ContentDetailRoute.self) { route in
                ContentDetailView(
                    contentId: route.contentId,
                    contentType: route.contentType,
                    allContentIds: route.allContentIds,
                    navigationSurface: route.navigationSurface
                )
                .dynamicTypeSize(contentTextSize)
                .environmentObject(readingStateStore)
            }
            .navigationDestination(for: ChatSessionRoute.self) { route in
                ChatSessionView(
                    route: route,
                    onShowHistory: tab == .knowledge
                        ? {
                            // Pop back to hub root, then push history
                            path.wrappedValue = NavigationPath()
                            path.wrappedValue.append(SessionHistoryRoute())
                        }
                        : nil
                )
                .id(route.stableKey)
            }
            .navigationDestination(for: SessionHistoryRoute.self) { _ in
                ChatSessionHistoryView(onSelectSession: { route in
                    path.wrappedValue.append(route)
                })
            }
            .navigationDestination(for: KnowledgeLibraryRoute.self) { _ in
                KnowledgeLibraryView()
            }
    }
}
