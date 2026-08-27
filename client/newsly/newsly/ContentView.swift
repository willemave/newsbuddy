//
//  ContentView.swift
//  newsly

import os.log
import SwiftUI

private let logger = Logger(subsystem: "com.newsly", category: "ContentView")

struct ContentView: View {
    private let authenticatedUserID: Int?

    @State private var tabCoordinator: TabCoordinatorViewModel
    @State private var knowledgeViewModel: KnowledgeTimelineViewModel

    @State private var readingStateStore: ReadingStateStore
    @State private var readStateCache: ReadStateCache
    @State private var submissionStatusViewModel: SubmissionStatusViewModel
    @State private var settings = AppSettings.shared
    @State private var chatSessionManager = ActiveChatSessionManager.shared
    @State private var chatNavigation = ChatNavigationCoordinator.shared
    @State private var e2eRouteInjector = E2ERouteInjector()
    @State private var briefingPath = NavigationPath()
    @State private var knowledgePath = NavigationPath()
    @State private var morePath = NavigationPath()
    @State private var showMoreSheet = false
    @State private var isMoreSheetActive = false
    @State private var compactTabBarHeight: CGFloat = 0
    @Environment(\.scenePhase) private var scenePhase

    @MainActor
    init(userId: Int? = nil, tabCoordinator: TabCoordinatorViewModel? = nil) {
        self.authenticatedUserID = userId
        let readStateCache = ReadStateCache()
        _readingStateStore = State(initialValue: ReadingStateStore(userId: userId))
        _readStateCache = State(initialValue: readStateCache)
        _tabCoordinator = State(
            initialValue: tabCoordinator ?? RootDependencyFactory.makeTabCoordinator(userID: userId)
        )
        _knowledgeViewModel = State(
            initialValue: RootDependencyFactory.makeKnowledgeTimelineViewModel(
                readStateCache: readStateCache
            )
        )
        _submissionStatusViewModel = State(
            initialValue: RootDependencyFactory.makeSubmissionStatusViewModel()
        )
    }

    var body: some View {
        @Bindable var tabCoordinator = tabCoordinator
        TabView(selection: $tabCoordinator.selectedTab) {
            Tab("Briefing", systemImage: "newspaper", value: RootTab.briefing) {
                BriefingTab(
                    path: $briefingPath,
                    scrollToTopRequest: tabCoordinator.scrollToTopRequest(for: .briefing),
                    viewModel: tabCoordinator.briefingVM,
                    readingStateStore: readingStateStore,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize
                )
            }

            Tab("Knowledge", systemImage: "books.vertical.fill", value: RootTab.knowledge) {
                KnowledgeTab(
                    path: $knowledgePath,
                    scrollToTopRequest: tabCoordinator.scrollToTopRequest(for: .knowledge),
                    viewModel: knowledgeViewModel,
                    readStateCache: readStateCache,
                    readingStateStore: readingStateStore,
                    contentTextSize: contentTextSize,
                    onOpenMore: { showMoreSheet = true },
                    onSelectSession: openChatSession
                )
            }
        }
        .environment(
            \.persistentBottomChromeInset,
            isCompactTabBarVisible ? compactTabBarHeight : 0
        )
        .safeAreaInset(edge: .bottom, spacing: 0) {
            RootCompactTabBarInset(
                selectedTab: tabCoordinator.selectedTab,
                isVisible: isCompactTabBarVisible,
                onSelect: selectRootTab
            )
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.height
            } action: { _, height in
                compactTabBarHeight = max(height, 0)
            }
        }
        .sheet(isPresented: $showMoreSheet, onDismiss: {
            isMoreSheetActive = false
            morePath = NavigationPath()
            drainPendingChatRoute()
        }) {
            NavigationStack(path: $morePath) {
                MoreView(
                    submissionsViewModel: submissionStatusViewModel,
                    readStateCache: readStateCache,
                    showsDismissButton: true
                )
                .withContentRoutes(
                    path: $morePath,
                    readingStateStore: readingStateStore,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize
                )
            }
            .presentationDragIndicator(.visible)
            .onAppear {
                isMoreSheetActive = true
            }
        }
        .tint(Color.appChromeAccent)
        .font(.appBody)
        .dynamicTypeSize(appTextSize)
        .environment(readingStateStore)
        .environment(readStateCache)
        .onAppear {
            AppChrome.configure(textSize: appTextSize)
            chatSessionManager.setPollingSuspended(scenePhase != .active)
            updateBriefingActivity()
            applyE2ERoutesIfNeeded()
            drainPendingChatRoute()
        }
        .onChange(of: tabCoordinator.selectedTab) { _, newValue in
            logger.info("[TabChange] selectedTab=\(String(describing: newValue), privacy: .public)")
            updateBriefingActivity()
        }
        .onChange(of: settings.appTextSizeIndex) { _, _ in
            AppChrome.configure(textSize: appTextSize)
        }
        .onChange(of: briefingPath.count) { oldValue, newValue in
            logRootPathChange(tab: .briefing, oldDepth: oldValue, newDepth: newValue)
        }
        .onChange(of: knowledgePath.count) { oldValue, newValue in
            logRootPathChange(tab: .knowledge, oldDepth: oldValue, newDepth: newValue)
            if oldValue > 0, newValue == 0 {
                if let presentedRoute = chatNavigation.presentedRoute {
                    chatNavigation.acknowledgePresented(presentedRoute)
                }
                drainPendingChatRoute()
            }
        }
        .onChange(of: scenePhase) { _, newPhase in
            chatSessionManager.setPollingSuspended(newPhase != .active)
            updateBriefingActivity()
            if newPhase == .active {
                applyE2ERoutesIfNeeded()
            }
        }
        .onChange(of: chatNavigation.queuedRoute) { _, route in
            guard route != nil else { return }
            drainPendingChatRoute()
        }
        .task {
            await BadgeStatsStore.shared.refreshStats()
            await submissionStatusViewModel.load()
        }
        .onDisappear {
            tabCoordinator.briefingVM.setActive(false)
        }
    }

    private var contentTextSize: DynamicTypeSize {
        ContentTextSize(index: settings.contentTextSizeIndex).dynamicTypeSize
    }

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    private func updateBriefingActivity() {
        let isActive = authenticatedUserID != nil
            && tabCoordinator.selectedTab == .briefing
            && scenePhase == .active
        tabCoordinator.briefingVM.setActive(isActive)
    }

    private func selectRootTab(_ tab: RootTab) {
        tabCoordinator.select(tab)
    }

    private var isCompactTabBarVisible: Bool {
        switch tabCoordinator.selectedTab {
        case .briefing:
            briefingPath.isEmpty
        case .knowledge:
            knowledgePath.isEmpty
        }
    }

    private func logRootPathChange(tab: RootTab, oldDepth: Int, newDepth: Int) {
        logger.info(
            "[Navigation] pathChanged tab=\(tab.rawValue, privacy: .public) oldCount=\(oldDepth, privacy: .public) newCount=\(newDepth, privacy: .public)"
        )
    }

    private func openChatSession(route: ChatSessionRoute) {
        chatNavigation.open(route)
        guard !showMoreSheet, !isMoreSheetActive else {
            showMoreSheet = false
            return
        }
        drainPendingChatRoute()
    }

    private func drainPendingChatRoute() {
        guard let route = chatNavigation.queuedRoute else { return }
        guard !showMoreSheet, !isMoreSheetActive else {
            showMoreSheet = false
            return
        }
        let replacesBackgroundChat = chatNavigation.presentedRoute != nil
            && tabCoordinator.selectedTab != .knowledge
        let replacesActiveNavigation = chatNavigation.queuedRouteReplacesCurrentNavigation
        guard knowledgePath.isEmpty || replacesBackgroundChat || replacesActiveNavigation else {
            return
        }
        guard chatNavigation.beginPresentation(
            route,
            replacingPresented: replacesBackgroundChat || replacesActiveNavigation
        ) else { return }
        logger.info("[Navigation] openChatSession sessionId=\(route.sessionId, privacy: .public)")
        presentChatSession(route)
    }

    private func presentChatSession(_ route: ChatSessionRoute) {
        if tabCoordinator.selectedTab == .briefing {
            briefingPath = NavigationPath()
        }
        knowledgePath = navigationPath(containing: route)
        tabCoordinator.selectedTab = .knowledge
    }

    private func openContentRoute(_ route: ContentDetailRoute) {
        briefingPath = navigationPath(containing: route)
        tabCoordinator.selectedTab = .briefing
    }

    private func navigationPath<Value: Hashable>(containing value: Value) -> NavigationPath {
        var path = NavigationPath()
        path.append(value)
        return path
    }

    private func applyE2ERoutesIfNeeded() {
        e2eRouteInjector.applyOpenContentRouteIfNeeded(openContentRoute: openContentRoute)
        e2eRouteInjector.applyOpenChatRouteIfNeeded(openChatSession: openChatSession)
    }
}
