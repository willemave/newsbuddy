//
//  ContentView.swift
//  newsly

import os.log
import SwiftUI

private let logger = Logger(subsystem: "com.newsly", category: "ContentView")

struct ContentView: View {
    private let authenticatedUserID: Int?

    @State private var tabCoordinator: TabCoordinatorViewModel
    @State private var learningHubViewModel: LearningHubViewModel

    @State private var readingStateStore: ReadingStateStore
    @State private var readStateCache: ReadStateCache
    @State private var submissionStatusViewModel: SubmissionStatusViewModel
    @State private var settings = AppSettings.shared
    @State private var unreadCountService = UnreadCountService.shared
    @State private var processingCountService = ProcessingCountService.shared
    @State private var chatSessionManager = ActiveChatSessionManager.shared
    @State private var chatNavigation = ChatNavigationCoordinator.shared
    @State private var e2eRouteInjector = E2ERouteInjector()
    @State private var longFormPath = NavigationPath()
    @State private var shortFormPath = NavigationPath()
    @State private var briefingPath = NavigationPath()
    @State private var knowledgePath = NavigationPath()
    @State private var learningPath = NavigationPath()
    @State private var morePath = NavigationPath()
    @State private var isRestoringPath = false
    @State private var learningFocusRequest: LearningFocusRequest?
    @State private var showMoreSheet = false
    @State private var longFormScrollToTopRequest = 0
    @State private var shortFormScrollToTopRequest = 0
    @State private var tabRetapFeedbackTrigger = 0
    @State private var compactTabBarHeight: CGFloat = 0
    @Environment(\.scenePhase) private var scenePhase

    @MainActor
    init(userId: Int? = nil, tabCoordinator: TabCoordinatorViewModel? = nil) {
        self.authenticatedUserID = userId
        let readStateCache = ReadStateCache()
        _readingStateStore = State(initialValue: ReadingStateStore(userId: userId))
        _readStateCache = State(initialValue: readStateCache)
        _tabCoordinator = State(
            initialValue: tabCoordinator ?? RootDependencyFactory.makeTabCoordinator(
                userID: userId,
                readStateCache: readStateCache
            )
        )
        _learningHubViewModel = State(initialValue: RootDependencyFactory.makeLearningHubViewModel())
        _submissionStatusViewModel = State(
            initialValue: RootDependencyFactory.makeSubmissionStatusViewModel()
        )
    }

    var body: some View {
        TabView(selection: tabSelection.binding) {
            if isBriefingExperience {
                BriefingTab(
                    path: $briefingPath,
                    viewModel: tabCoordinator.briefingVM,
                    readingStateStore: readingStateStore,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize
                )
            } else {
                LongFormTab(
                    path: $longFormPath,
                    viewModel: tabCoordinator.longContentVM,
                    isActive: tabCoordinator.selectedTab == .longContent,
                    badge: longBadge,
                    readingStateStore: readingStateStore,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize,
                    scrollToTopRequest: longFormScrollToTopRequest,
                    onShowNarrations: openKnowledgeNarrations,
                    currentFastReadItems: { tabCoordinator.shortNewsVM.currentItems() }
                )

                ShortFormTab(
                    path: $shortFormPath,
                    viewModel: tabCoordinator.shortNewsVM,
                    isActive: tabCoordinator.selectedTab == .shortNews,
                    badge: shortBadge,
                    readingStateStore: readingStateStore,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize,
                    scrollToTopRequest: shortFormScrollToTopRequest
                )
            }

            KnowledgeTab(
                path: $knowledgePath,
                isBriefingExperience: isBriefingExperience,
                readStateCache: readStateCache,
                readingStateStore: readingStateStore,
                contentTextSize: contentTextSize,
                onOpenMore: { showMoreSheet = true }
            )

            LearningTab(
                path: $learningPath,
                focusRequest: $learningFocusRequest,
                isBriefingExperience: isBriefingExperience,
                viewModel: learningHubViewModel,
                readStateCache: readStateCache,
                readingStateStore: readingStateStore,
                contentTextSize: contentTextSize,
                onOpenMore: { showMoreSheet = true }
            )

            if !isBriefingExperience {
                MoreTab(
                    path: $morePath,
                    submissionsViewModel: submissionStatusViewModel,
                    readStateCache: readStateCache,
                    readingStateStore: readingStateStore,
                    contentTextSize: contentTextSize,
                    badge: moreBadge
                )
            }
        }
        .environment(
            \.persistentBottomChromeInset,
            isBriefingExperience && isCompactTabBarVisible ? compactTabBarHeight : 0
        )
        .safeAreaInset(edge: .bottom, spacing: 0) {
            BriefingCompactTabBarInset(
                selectedTab: tabCoordinator.selectedTab,
                isVisible: isBriefingExperience && isCompactTabBarVisible,
                onSelect: tabSelection.select
            )
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.height
            } action: { _, height in
                compactTabBarHeight = max(height, 0)
            }
        }
        .sheet(isPresented: $showMoreSheet, onDismiss: {
            morePath = NavigationPath()
        }) {
            NavigationStack(path: $morePath) {
                MoreView(
                    submissionsViewModel: submissionStatusViewModel,
                    readStateCache: readStateCache,
                    showsDismissButton: true
                )
                .withContentRoutes(
                    tab: .more,
                    path: $morePath,
                    readingStateStore: readingStateStore,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize
                )
            }
            .presentationDragIndicator(.visible)
        }
        .tint(Color.appChromeAccent)
        .font(.appBody)
        .dynamicTypeSize(appTextSize)
        .sensoryFeedback(.impact(weight: .light), trigger: tabRetapFeedbackTrigger)
        .environment(readingStateStore)
        .environment(readStateCache)
        .onAppear {
            AppChrome.configure(textSize: appTextSize)
            chatSessionManager.setPollingSuspended(scenePhase != .active)
            unreadCountService.setPeriodicRefreshSuspended(scenePhase != .active)
            processingCountService.setPeriodicRefreshSuspended(scenePhase != .active)
            tabSelection.reconcile()
            tabCoordinator.ensureInitialLoads()
            updateBriefingActivity()
            restoreIfNeeded()
            applyE2ERoutesIfNeeded()
        }
        .onChange(of: tabCoordinator.selectedTab) { _, newValue in
            logger.info("[TabChange] selectedTab=\(String(describing: newValue), privacy: .public)")
            tabCoordinator.handleTabChange(to: newValue)
            updateBriefingActivity()
        }
        .onChange(of: settings.readingExperienceRaw) { _, _ in
            tabSelection.reconcile()
            updateBriefingActivity()
        }
        .onChange(of: settings.appTextSizeIndex) { _, _ in
            AppChrome.configure(textSize: appTextSize)
        }
        .onChange(of: longFormPath.count) { oldValue, newValue in
            logRootPathChange(tab: .longContent, oldDepth: oldValue, newDepth: newValue)
        }
        .onChange(of: shortFormPath.count) { oldValue, newValue in
            logRootPathChange(tab: .shortNews, oldDepth: oldValue, newDepth: newValue)
        }
        .onChange(of: briefingPath.count) { oldValue, newValue in
            logRootPathChange(tab: .briefing, oldDepth: oldValue, newDepth: newValue)
        }
        .onChange(of: knowledgePath.count) { oldValue, newValue in
            logRootPathChange(tab: .knowledge, oldDepth: oldValue, newDepth: newValue)
        }
        .onChange(of: learningPath.count) { oldValue, newValue in
            logRootPathChange(tab: .learning, oldDepth: oldValue, newDepth: newValue)
        }
        .onChange(of: scenePhase) { _, newPhase in
            chatSessionManager.setPollingSuspended(newPhase != .active)
            unreadCountService.setPeriodicRefreshSuspended(newPhase != .active)
            processingCountService.setPeriodicRefreshSuspended(newPhase != .active)
            updateBriefingActivity()
            if newPhase == .active {
                restoreIfNeeded()
                applyE2ERoutesIfNeeded()
            }
        }
        .onChange(of: chatNavigation.pendingRoute) { _, route in
            guard let route else { return }
            logger.info("[Navigation] openChatSession sessionId=\(route.sessionId, privacy: .public)")
            openChatSession(route: route)
            chatNavigation.clear(route: route)
        }
        .task {
            await unreadCountService.refreshCounts()
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

    private var isBriefingExperience: Bool {
        settings.readingExperience == .briefing
    }

    private func updateBriefingActivity() {
        let isActive = authenticatedUserID != nil
            && isBriefingExperience
            && tabCoordinator.selectedTab == .briefing
            && scenePhase == .active
        tabCoordinator.briefingVM.setActive(isActive)
    }

    private var longBadge: Int {
        max(unreadCountService.articleCount + unreadCountService.podcastCount, 0)
    }

    private var shortBadge: Int {
        max(unreadCountService.newsCount, 0)
    }

    private var moreBadge: Int {
        max(submissionStatusViewModel.unseenCount, 0)
    }

    private var tabSelection: RootTabSelectionModel {
        RootTabSelectionModel(
            tabCoordinator: tabCoordinator,
            isBriefingExperience: isBriefingExperience,
            longFormPathIsEmpty: longFormPath.isEmpty,
            shortFormPathIsEmpty: shortFormPath.isEmpty,
            onLongFormRetap: { requestScrollToTop($longFormScrollToTopRequest) },
            onShortFormRetap: { requestScrollToTop($shortFormScrollToTopRequest) }
        )
    }

    private var isCompactTabBarVisible: Bool {
        switch tabCoordinator.selectedTab {
        case .briefing:
            briefingPath.isEmpty
        case .knowledge:
            knowledgePath.isEmpty
        case .learning:
            learningPath.isEmpty
        case .longContent, .shortNews, .more:
            false
        }
    }

    private func logRootPathChange(tab: RootTab, oldDepth: Int, newDepth: Int) {
        logger.info(
            "[Navigation] pathChanged tab=\(tab.logName, privacy: .public) oldCount=\(oldDepth, privacy: .public) newCount=\(newDepth, privacy: .public)"
        )
    }

    private func restoreIfNeeded() {
        NavigationRestorationModel.restoreIfNeeded(
            isBriefingExperience: isBriefingExperience,
            isRestoringPath: $isRestoringPath,
            readingStateStore: readingStateStore,
            tabCoordinator: tabCoordinator,
            shortFormPath: $shortFormPath,
            longFormPath: $longFormPath
        )
    }

    private func openChatSession(route: ChatSessionRoute) {
        if tabCoordinator.selectedTab == .briefing {
            briefingPath = NavigationPath()
        }
        learningPath = navigationPath(containing: route)
        tabCoordinator.selectedTab = .learning
    }

    private func openKnowledgeNarrations() {
        learningPath = NavigationPath()
        learningFocusRequest = LearningFocusRequest(target: .narrations)
        tabCoordinator.selectedTab = .learning
    }

    private func openContentRoute(_ route: ContentDetailRoute) {
        switch route.contentType {
        case .news:
            shortFormPath = navigationPath(containing: route)
            tabCoordinator.selectedTab = .shortNews
        case .article, .podcast, .insight_report, .unknown, .unknownRaw:
            longFormPath = navigationPath(containing: route)
            tabCoordinator.selectedTab = .longContent
        }
    }

    private func navigationPath<Value: Hashable>(containing value: Value) -> NavigationPath {
        var path = NavigationPath()
        path.append(value)
        return path
    }

    private func requestScrollToTop(_ request: Binding<Int>) {
        request.wrappedValue += 1
        tabRetapFeedbackTrigger += 1
    }

    private func applyE2ERoutesIfNeeded() {
        e2eRouteInjector.applyOpenContentRouteIfNeeded(openContentRoute: openContentRoute)
        e2eRouteInjector.applyOpenChatRouteIfNeeded(openChatSession: openChatSession)
    }
}
