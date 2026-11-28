//
//  ContentView.swift
//  newsly

import os.log
import SwiftUI

private let logger = Logger(subsystem: "com.newsly", category: "ContentView")

struct ContentView: View {
    @Environment(AppLifecycle.self) private var lifecycle
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    private let session: AuthenticatedSession
    private let tabCoordinator: TabCoordinatorViewModel
    private let knowledgeViewModel: KnowledgeTimelineViewModel
    private let readStateCache: ReadStateCache
    private let submissionStatusViewModel: SubmissionStatusViewModel
    private let chatNavigation: ChatNavigationCoordinator
    private let dependencyFactory: RootDependencyFactory

    @State private var briefingSourceMutation = 0
    @State private var settings: AppSettings
    @State private var e2eRouteInjector = E2ERouteInjector()
    /// Typed and at most one deep; it is the Briefing selection in both the
    /// compact push and the open-display detail pane.
    @State private var briefingPath: [ContentDetailRoute] = []
    /// The item a pushed Briefing detail shows after the reader pages, so a
    /// layout change carries the item on screen rather than the one opened.
    @State private var briefingVisibleContentId: Int?
    @State private var knowledgePath = NavigationPath()
    @State private var settingsPath = NavigationPath()
    @State private var showSettingsSheet = false
    @State private var isSettingsSheetActive = false
    @State private var compactTabBarHeight: CGFloat = 0
    @State private var tabBarMinimizer = RootTabBarMinimizer()

    @MainActor
    init(session: AuthenticatedSession) {
        self.session = session
        self.tabCoordinator = session.tabCoordinator
        self.knowledgeViewModel = session.knowledgeViewModel
        self.readStateCache = session.readStateCache
        self.submissionStatusViewModel = session.submissionStatusViewModel
        self.chatNavigation = session.chatNavigation
        self.dependencyFactory = session.dependencyFactory
        _settings = State(initialValue: session.dependencyFactory.appSettings)
    }

    /// While the app is not active the system snapshots it at other sizes,
    /// briefly reporting compact size classes on the open display. The layout
    /// holds still then, so leaving and returning never swaps shells (which
    /// would rebuild the detail and reload it).
    @State private var heldLayout: RootLayout?

    private var rootLayout: RootLayout {
        heldLayout ?? RootLayout.resolve(horizontal: horizontalSizeClass, vertical: verticalSizeClass)
    }

    var body: some View {
        rootShell
        // The floating bar leaves the home-indicator area exposed. Paint it at
        // the root so it matches the reading surface instead of the system host.
        .background(Color.surfacePrimary.ignoresSafeArea())
        .attachedChatHost(
            .root,
            controller: session.attachedChat,
            presentsAsSheet: rootLayout == .compact,
            contentTextSize: contentTextSize
        )
        #if DEBUG
        .overlay { FoldDiagnosticsOverlay() }
        #endif
        .sheet(isPresented: $showSettingsSheet, onDismiss: {
            isSettingsSheetActive = false
            // A chat attached inside Settings stays open over the root.
            session.attachedChat.move(from: .settings, to: .root)
            settingsPath = NavigationPath()
            drainPendingChatRoute()
        }) {
            NavigationStack(path: $settingsPath) {
                SettingsView(showsDismissButton: true)
                .withContentRoutes(
                    path: $settingsPath,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize,
                    chatHistoryViewModel: knowledgeViewModel.chats
                )
            }
            .attachedChatHost(
                .settings,
                controller: session.attachedChat,
                contentTextSize: contentTextSize
            )
            .presentationDragIndicator(.visible)
            .onAppear {
                isSettingsSheetActive = true
            }
        }
        .task(id: briefingSourceMutation) {
            guard briefingSourceMutation > 0 else { return }
            await session.knowledgeViewModel.forceReload()
        }
        .tint(Color.appChromeAccent)
        .font(.appBody)
        .dynamicTypeSize(appTextSize)
        .environment(readStateCache)
        .environment(session.badgeStatsStore)
        .environment(session.xConnectionStore)
        .environment(submissionStatusViewModel)
        .environment(session.activeChatSessionManager)
        .environment(session.chatNavigation)
        .environment(session.attachedChat)
        .environment(session.briefingSavedSources)
        .onAppear {
            AppChrome.configure(textSize: appTextSize)
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
        .onChange(of: briefingPath) { oldValue, newValue in
            logRootPathChange(tab: .briefing, oldDepth: oldValue.count, newDepth: newValue.count)
            if oldValue.last != newValue.last {
                briefingVisibleContentId = nil
            }
        }
        .onChange(of: rootLayout) { oldLayout, newLayout in
            handleLayoutChange(from: oldLayout, to: newLayout)
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
        .onChange(of: lifecycle.phase) { _, newPhase in
            heldLayout = newPhase == .active ? nil : rootLayout
            updateBriefingActivity()
            if newPhase == .active {
                applyE2ERoutesIfNeeded()
            }
        }
        .onChange(of: chatNavigation.queuedRoute) { _, route in
            guard route != nil else { return }
            drainPendingChatRoute()
        }
        .onDisappear {
            tabCoordinator.briefingVM.setActive(false)
        }
    }

    @ViewBuilder
    private var rootShell: some View {
        switch rootLayout {
        case .compact:
            compactShell
        case .openDisplay:
            OpenDisplayShell(
                session: session,
                briefingPath: openDisplayBriefingPath,
                knowledgePath: $knowledgePath,
                contentTextSize: contentTextSize,
                onSourceMutation: { briefingSourceMutation += 1 },
                onOpenSettings: { showSettingsSheet = true },
                onSelectSession: openChatSession
            )
        }
    }

    /// The open shell reads the Briefing selection already re-targeted at
    /// the item a compact detail paged to, so the pane never loads the
    /// originally opened item first.
    private var openDisplayBriefingPath: Binding<[ContentDetailRoute]> {
        Binding(
            get: {
                guard let route = briefingPath.last else { return briefingPath }
                return [retargetedBriefingSelection(route, visibleContentId: briefingVisibleContentId)]
            },
            set: { briefingPath = $0 }
        )
    }

    private var compactShell: some View {
        @Bindable var tabCoordinator = tabCoordinator
        return TabView(selection: $tabCoordinator.selectedTab) {
            Tab("Briefing", systemImage: "newspaper", value: RootTab.briefing) {
                BriefingTab(
                    path: $briefingPath,
                    scrollToTopRequest: tabCoordinator.scrollToTopRequest(for: .briefing),
                    viewModel: tabCoordinator.briefingVM,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize,
                    dependencyFactory: dependencyFactory,
                    onSourceMutation: { briefingSourceMutation += 1 }
                )
                .environment(\.onDetailVisibleContentChange) { contentId in
                    briefingVisibleContentId = contentId
                }
            }

            Tab("Knowledge", systemImage: "books.vertical.fill", value: RootTab.knowledge) {
                KnowledgeTab(
                    path: $knowledgePath,
                    scrollToTopRequest: tabCoordinator.scrollToTopRequest(for: .knowledge),
                    isSelectedRootTab: tabCoordinator.selectedTab == .knowledge,
                    viewModel: knowledgeViewModel,
                    readStateCache: readStateCache,
                    contentTextSize: contentTextSize,
                    dependencyFactory: dependencyFactory,
                    onOpenSettings: { showSettingsSheet = true },
                    onSelectSession: openChatSession
                )
            }
        }
        .environment(tabBarMinimizer)
        .environment(
            \.persistentBottomChromeInset,
            isCompactTabBarVisible ? compactTabBarHeight : 0
        )
        .onChange(of: isCompactTabBarVisible) { _, _ in
            tabBarMinimizer.expand()
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            RootCompactTabBarInset(
                selectedTab: tabCoordinator.selectedTab,
                isVisible: isCompactTabBarVisible,
                minimizer: tabBarMinimizer,
                onSelect: selectRootTab
            )
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.height
            } action: { _, height in
                compactTabBarHeight = max(height, 0)
            }
        }
    }

    /// Opening or closing the phone swaps shells. The Briefing selection is
    /// re-targeted at the item a pushed detail paged to.
    private func handleLayoutChange(from oldLayout: RootLayout, to newLayout: RootLayout) {
        logger.info("[Layout] rootLayout old=\(String(describing: oldLayout), privacy: .public) new=\(String(describing: newLayout), privacy: .public)")
        if let route = briefingPath.last {
            let retargeted = retargetedBriefingSelection(route, visibleContentId: briefingVisibleContentId)
            if retargeted != route {
                briefingPath = [retargeted]
            }
        }
        briefingVisibleContentId = nil
        // The reader lives inside the detail of the shell being replaced, so
        // its chat moves to the root rather than losing its host (B22).
        session.attachedChat.move(from: .reader, to: .root)
    }

    private var contentTextSize: DynamicTypeSize {
        ContentTextSize(index: settings.contentTextSizeIndex).dynamicTypeSize
    }

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    private func updateBriefingActivity() {
        let isBriefingVisible = tabCoordinator.selectedTab == .briefing

        switch lifecycle.phase {
        case .active:
            tabCoordinator.briefingVM.setActive(isBriefingVisible)
        case .inactive:
            // Preserve selected-Lens work through a temporary interruption, but
            // still deactivate when the user leaves the Briefing tab.
            if !isBriefingVisible {
                tabCoordinator.briefingVM.setActive(false)
            }
        case .background:
            tabCoordinator.briefingVM.setActive(false)
        }
    }

    private func selectRootTab(_ tab: RootTab) {
        tabBarMinimizer.expand()
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
        guard !showSettingsSheet, !isSettingsSheetActive else {
            showSettingsSheet = false
            return
        }
        drainPendingChatRoute()
    }

    private func drainPendingChatRoute() {
        guard let route = chatNavigation.queuedRoute else { return }
        guard !showSettingsSheet, !isSettingsSheetActive else {
            showSettingsSheet = false
            return
        }
        let replacesBackgroundChat = chatNavigation.presentedRoute != nil
            && tabCoordinator.selectedTab != .knowledge
        // On the open display the Knowledge path is the Knowledge pane, and
        // its first item has no back button, so a new global chat replaces
        // whatever the pane shows instead of waiting for it to empty.
        let replacesOpenDisplayPane = rootLayout == .openDisplay
        guard knowledgePath.isEmpty || replacesBackgroundChat || replacesOpenDisplayPane else {
            return
        }
        guard chatNavigation.beginPresentation(
            route,
            replacingPresented: replacesBackgroundChat || replacesOpenDisplayPane
        ) else { return }
        logger.info("[Navigation] openChatSession sessionId=\(route.sessionId, privacy: .public)")
        presentChatSession(route)
        // The pane never empties (sidebar picks replace it), so nothing would
        // acknowledge the route later and the same chat could not be picked
        // again after viewing something else.
        if replacesOpenDisplayPane {
            chatNavigation.acknowledgePresented(route)
        }
    }

    private func presentChatSession(_ route: ChatSessionRoute) {
        // A global chat replaces whatever chat was attached to content.
        session.attachedChat.close()
        if tabCoordinator.selectedTab == .briefing {
            briefingPath = []
        }
        knowledgePath = NavigationPath([route])
        tabCoordinator.selectedTab = .knowledge
    }

    private func openContentRoute(_ route: ContentDetailRoute) {
        briefingPath = [route]
        tabCoordinator.selectedTab = .briefing
    }

    private func applyE2ERoutesIfNeeded() {
        e2eRouteInjector.applyBriefingLensIfNeeded(viewModel: tabCoordinator.briefingVM)
        e2eRouteInjector.applyOpenScreenIfNeeded(
            selectKnowledge: { tabCoordinator.select(.knowledge) },
            openSettings: { showSettingsSheet = true },
            openChatHistory: {
                knowledgePath = NavigationPath([SessionHistoryRoute()])
                tabCoordinator.select(.knowledge)
            }
        )
        e2eRouteInjector.applyOpenContentRouteIfNeeded(openContentRoute: openContentRoute)
        e2eRouteInjector.applyOpenChatRouteIfNeeded(
            openChatSession: openChatSession,
            openAttachedChat: { route in session.attachedChat.present(route, host: .root) }
        )
    }
}
