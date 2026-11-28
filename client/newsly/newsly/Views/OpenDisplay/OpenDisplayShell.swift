//
//  OpenDisplayShell.swift
//  newsly
//

import SwiftUI

/// The open iPhone Duo layout: a tight icon rail, then the selected tier.
///
/// - News reads in one column centred in the full view. Opening a story
///   moves News to a leading column ending 40 pt before the hinge, with the
///   story beside it.
/// - Podcasts, Articles, and Knowledge are a 300 pt sidebar list with the
///   selected item flush beside it and an interstitial before the first pick.
/// - Nothing overlays content: attached chats take a page of their own.
/// - The status bar and camera float over content; no column is reserved.
///
/// Selection lives above this shell (`ContentView`'s typed Briefing path and
/// Knowledge path, `TabCoordinatorViewModel`, `BriefingViewModel`), so opening
/// or closing the phone swaps shells without losing the item on screen.
struct OpenDisplayShell: View {
    let session: AuthenticatedSession
    @Binding var briefingPath: [ContentDetailRoute]
    @Binding var knowledgePath: NavigationPath
    let contentTextSize: DynamicTypeSize
    let onSourceMutation: () -> Void
    let onOpenSettings: () -> Void
    let onSelectSession: (ChatSessionRoute) -> Void

    @State private var contentWidth: CGFloat = 0
    @State private var contentHeight: CGFloat = 0
    /// The system's fold, in the content area's coordinates (iOS 27.1+).
    @State private var foldFrame: CGRect?
    @Namespace private var chatTransitionNamespace

    private var tabCoordinator: TabCoordinatorViewModel { session.tabCoordinator }
    private var briefing: BriefingViewModel { session.tabCoordinator.briefingVM }
    private var attachedChat: AttachedChatController { session.attachedChat }

    private var inlineChat: AttachedChatController.Presentation? {
        guard let presentation = attachedChat.presentation, presentation.host == .root else { return nil }
        return presentation
    }

    /// The chat renders only in the visible area. Both areas stay mounted, so
    /// rendering it in each would give one session two live views (two loads,
    /// two polls).
    private func inlineChat(in tab: RootTab) -> AttachedChatController.Presentation? {
        tabCoordinator.selectedTab == tab ? inlineChat : nil
    }

    var body: some View {
        HStack(spacing: 0) {
            OpenDisplayRail(
                briefingItems: briefing.railItems,
                knowledgeItem: knowledgeRailItem,
                selection: railSelection,
                player: briefing.railPlayerState,
                onSelect: selectRail,
                onSearch: openKnowledgeSearch,
                onOpenSettings: onOpenSettings,
                settingsNeedsAttention: session.xConnectionStore.needsAttention
            )

            divider()

            contentArea
                // The status bar and camera float over content on the open
                // display; no column is reserved for them.
                .ignoresSafeArea(.container, edges: .trailing)
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        // The rail shows Briefing's tiers and badges even when Knowledge is
        // selected, so the index must load without Briefing being active.
        .task {
            await briefing.loadIndexIfNeeded()
        }
    }

    // MARK: Geometry

    /// Both areas stay mounted, as the phone's tabs do, so News keeps its
    /// place while the reader visits Knowledge and back. Only the selected
    /// one is visible, interactive, and read by VoiceOver.
    private var contentArea: some View {
        ZStack {
            briefingArea
                .openDisplayAreaVisibility(tabCoordinator.selectedTab == .briefing)
            knowledgeArea
                .openDisplayAreaVisibility(tabCoordinator.selectedTab == .knowledge)
        }
        .background { OpenDisplayFoldReader(foldFrame: $foldFrame) }
        .onGeometryChange(for: CGSize.self) { $0.size } action: { size in
            contentWidth = size.width
            contentHeight = size.height
        }
    }

    private var hingeX: CGFloat? {
        OpenDisplayMetrics.hingeX(foldFrame: foldFrame, contentHeight: contentHeight)
    }

    private var newsLeadingWidth: CGFloat {
        OpenDisplayMetrics.newsLeadingWidth(hingeX: hingeX, contentWidth: contentWidth)
    }

    private func divider() -> some View {
        Rectangle()
            .fill(Color.borderSubtle)
            .frame(width: 1)
            .ignoresSafeArea()
    }

    // MARK: Briefing

    private var isNewsLike: Bool {
        briefing.isNewsTierSelected || briefing.isStartHereSelected
    }

    private var arrangement: OpenBriefingArrangement {
        OpenBriefingArrangement.resolve(
            isNewsLike: isNewsLike,
            hasStory: briefingPath.last != nil,
            hasChat: inlineChat(in: .briefing) != nil
        )
    }

    /// News, Podcasts, or Articles in the leading slot, kept mounted across
    /// arrangements so its scroll position survives a story opening.
    private var briefingArea: some View {
        let arrangement = arrangement
        let leadingWidth = leadingWidth(for: arrangement)
        let isNewsObscured = arrangement == .chatBesideStory
        return HStack(spacing: 0) {
            ZStack {
                // A stack only for the empty-state and first-run links
                // ("All News", "Manage sources"); stories open beside it.
                NavigationStack {
                    BriefingView(
                        viewModel: briefing,
                        playbackService: session.dependencyFactory.narrationPlaybackService,
                        scrollToTopRequest: tabCoordinator.scrollToTopRequest(for: .briefing),
                        contentTextSize: contentTextSize,
                        layout: briefingLayout(for: arrangement),
                        isReadTrackingSuspended: isNewsObscured,
                        onOpenContent: openBriefingContent,
                        onSourceMutation: onSourceMutation
                    )
                    .extendsToOpenDisplayEdge()
                }
                .environment(\.isInOpenDisplayPane, true)
                .opacity(isNewsObscured ? 0 : 1)
                .allowsHitTesting(!isNewsObscured)
                .accessibilityHidden(isNewsObscured)

                if isNewsObscured, let chat = inlineChat(in: .briefing) {
                    chatPane(chat)
                }
            }
            .frame(width: leadingWidth)
            .frame(maxWidth: leadingWidth == nil ? .infinity : nil)

            if let trailing = trailingPane(for: arrangement) {
                divider()
                trailingContent(trailing, closable: isNewsLike)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .animation(AppMotion.subtle, value: arrangement)
    }

    /// Picking a Podcasts or Articles row shows it in place of any chat;
    /// opening a News story keeps the chat beside it (chat | story).
    private func openBriefingContent(_ route: ContentDetailRoute) {
        if !isNewsLike, inlineChat != nil {
            attachedChat.close()
        }
        briefingPath = [route]
    }

    private func briefingLayout(for arrangement: OpenBriefingArrangement) -> BriefingLayout {
        switch arrangement {
        case .newsAlone:
            .column(BriefingColumnLayout(readingWidth: OpenDisplayMetrics.newsReadingWidth))
        case .newsBeside, .chatBesideStory:
            .column(BriefingColumnLayout(selectedSourceKey: selectedBriefingSourceKey))
        case .listBeside:
            .column(BriefingColumnLayout(presentsCardsAsRows: true, selectedSourceKey: selectedBriefingSourceKey))
        }
    }

    private func leadingWidth(for arrangement: OpenBriefingArrangement) -> CGFloat? {
        switch arrangement {
        case .newsAlone: nil
        case .newsBeside, .chatBesideStory: newsLeadingWidth
        case .listBeside: OpenDisplayMetrics.sidebarWidth
        }
    }

    private func trailingPane(for arrangement: OpenBriefingArrangement) -> OpenTrailingPane? {
        switch arrangement {
        case .newsAlone: nil
        case .newsBeside(let pane), .listBeside(let pane): pane
        case .chatBesideStory: .detail
        }
    }

    @ViewBuilder
    private func trailingContent(_ pane: OpenTrailingPane, closable: Bool) -> some View {
        switch pane {
        case .detail:
            if let route = briefingPath.last {
                briefingDetail(route)
                    .overlay(alignment: .topLeading) {
                        if closable { closeButton }
                    }
            }
        case .chat:
            if let chat = inlineChat(in: .briefing) {
                chatPane(chat)
            }
        case .interstitial:
            briefingInterstitial
        }
    }

    /// News keeps a close control on the story so the reader can return to
    /// News on its own. It sits right of the hinge line.
    private var closeButton: some View {
        let paneMinX = newsLeadingWidth + 1
        let leading = hingeX.map { max($0 - paneMinX + 12, 14) } ?? 14
        return Button {
            briefingPath = []
        } label: {
            Image(systemName: "xmark")
                .font(.appSymbol(size: 15, weight: .semibold))
                .foregroundStyle(Color.onSurface)
                .frame(width: 36, height: 36)
                .glassEffect(.regular.interactive(), in: .circle)
                // A full 44 pt target around the 36 pt control.
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.leading, leading - 4)
        .padding(.top, 6)
        .accessibilityLabel("Close story")
        .accessibilityIdentifier("open.story.close")
    }

    /// No navigation stack: the detail never pushes, and a UIKit-backed
    /// stack would re-impose the status column's safe area under the pane.
    private func briefingDetail(_ route: ContentDetailRoute) -> some View {
        ContentDetailView(
            contentId: route.contentId,
            contentType: route.contentType,
            // Picking another source replaces paging.
            allContentIds: [route.contentId],
            navigationSurface: route.navigationSurface,
            initialScrollTarget: route.initialScrollTarget,
            readStateCache: session.readStateCache
        )
        .dynamicTypeSize(contentTextSize)
        .environment(\.isDetailPaneRoot, true)
        .environment(\.isInOpenDisplayPane, true)
        .id(route)
    }

    private func chatPane(_ presentation: AttachedChatController.Presentation) -> some View {
        AttachedChatPane(presentation: presentation, controller: attachedChat)
            .dynamicTypeSize(contentTextSize)
            .environment(\.isInOpenDisplayPane, true)
            .background(Color.surfacePrimary.ignoresSafeArea())
    }

    private var briefingInterstitial: OpenDisplayInterstitial {
        let lensKey = briefing.selectedLensKey
        let summary = briefing.selectedLensSummary
        let isPodcasts = summary?.tier == .audio
        let renderModel = lensKey.flatMap { briefing.renderModel(for: $0) }
        if let renderModel, renderModel.segments.isEmpty, !renderModel.hasMore {
            return OpenDisplayInterstitial(
                kind: .caughtUp,
                action: .init(title: "Refresh", systemImage: "arrow.clockwise") {
                    Task { await briefing.pullToRefresh() }
                }
            )
        }
        // Loaded unread sources only, to match the unread count beside it.
        let unreadSources = (briefing.selectedLens?.sources ?? []).filter { !$0.read }
        let totalSeconds = unreadSources.reduce(0) { total, source in
            total + (isPodcasts ? (source.durationSeconds ?? 0) : (source.readingMinutes ?? 0) * 60)
        }
        let lensTitle = summary?.title ?? (isPodcasts ? "Podcasts" : "Articles")
        return OpenDisplayInterstitial(
            kind: isPodcasts ? .podcasts : .articles,
            meta: openDisplayInterstitialMeta(
                isPodcasts: isPodcasts,
                unreadCount: summary?.unreadSourceCount ?? 0,
                totalSeconds: totalSeconds
            ),
            action: lensKey.map { key in
                OpenDisplayInterstitial.Action(
                    title: isPodcasts ? "Play the \(lensTitle) briefing" : "Listen to \(lensTitle)",
                    systemImage: isPodcasts ? "play.fill" : "headphones"
                ) {
                    Task { await briefing.narrationController.togglePlayback(for: key) }
                }
            }
        )
    }

    /// The source in the pane, matched within the selected lens so news and
    /// content ids that happen to coincide never highlight the wrong row.
    private var selectedBriefingSourceKey: String? {
        guard let route = briefingPath.last else { return nil }
        let wantsNews = route.contentType == .news
        return briefing.selectedLens?.sources.first { source in
            source.id == route.contentId && (source.kind == "news") == wantsNews
        }?.sourceKey
    }

    // MARK: Knowledge

    /// One Knowledge list, as on the phone (saved items, chats, decks), in
    /// the sidebar. The Knowledge navigation stack is the pane beside it, so
    /// global chat routes, search, and detail land there exactly as they push
    /// on compact.
    private var knowledgeArea: some View {
        HStack(spacing: 0) {
            KnowledgeView(
                scrollToTopRequest: tabCoordinator.scrollToTopRequest(for: .knowledge),
                isVisible: tabCoordinator.selectedTab == .knowledge,
                onSelectContent: { route in
                    closeInlineChat()
                    knowledgePath = NavigationPath([route])
                },
                onSelectSession: onSelectSession,
                onSearch: openKnowledgeSearch,
                onOpenSettings: onOpenSettings,
                viewModel: session.knowledgeViewModel,
                contentTextSize: contentTextSize,
                chatTransitionNamespace: chatTransitionNamespace,
                showsHeaderActions: false
            )
            .dynamicTypeSize(contentTextSize)
            .frame(width: OpenDisplayMetrics.sidebarWidth)

            divider()

            Group {
                if let chat = inlineChat(in: .knowledge) {
                    chatPane(chat)
                } else {
                    knowledgePane
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var knowledgePane: some View {
        NavigationStack(path: $knowledgePath) {
            OpenDisplayInterstitial(kind: .knowledge)
                .extendsToOpenDisplayEdge()
                .toolbar(.hidden, for: .navigationBar)
                .navigationDestination(for: KnowledgeSearchRoute.self) { _ in
                    KnowledgeSearchView(
                        onSelectContent: { route in knowledgePath.append(route) },
                        viewModel: session.dependencyFactory.makeContentListViewModel(
                            readStateCache: session.readStateCache
                        )
                    )
                }
                .withContentRoutes(
                    path: $knowledgePath,
                    readStateCache: session.readStateCache,
                    contentTextSize: contentTextSize,
                    chatTransitionNamespace: chatTransitionNamespace,
                    allowsChatHistory: true,
                    chatHistoryViewModel: session.knowledgeViewModel.chats
                )
        }
        // Only the pane's first screen drops its back button; anything pushed
        // above it (a search result, chat history) keeps one.
        .environment(\.isDetailPaneRoot, knowledgePath.count <= 1)
        .environment(\.isInOpenDisplayPane, true)
        .ignoresSafeArea(.container, edges: .trailing)
    }

    /// Search opens in the Knowledge pane, from any tier.
    private func openKnowledgeSearch() {
        closeInlineChat()
        knowledgePath = NavigationPath([KnowledgeSearchRoute()])
        if tabCoordinator.selectedTab != .knowledge {
            tabCoordinator.select(.knowledge)
        }
    }

    /// A chat attached to content gives way when the reader picks something
    /// else to look at.
    private func closeInlineChat() {
        if inlineChat != nil { attachedChat.close() }
    }

    // MARK: Rail

    private let knowledgeRailItem = OpenDisplayRail.Item(
        destination: .knowledge,
        title: "Knowledge",
        systemImage: "books.vertical",
        badge: 0,
        accessibilityIdentifier: "open.rail.knowledge"
    )

    private var railSelection: OpenRailDestination? {
        OpenRailDestination.current(
            selectedTab: tabCoordinator.selectedTab,
            isStartHereSelected: briefing.isStartHereSelected,
            isNewsTierSelected: briefing.isNewsTierSelected,
            selectedLensKey: briefing.selectedLensKey
        )
    }

    /// Writes the root and Briefing owners, so activity, read tracking, and
    /// chat routing behave exactly as on compact. Reselecting the current item
    /// scrolls its list to the top.
    private func selectRail(_ destination: OpenRailDestination) {
        let isReselection = destination == railSelection
        // The Briefing tier as it was, even if Knowledge is showing now.
        let previousBriefingDestination = briefing.railSelection
        // An attached chat belongs beside its source; another destination
        // hands it to background tracking.
        if !isReselection {
            closeInlineChat()
        }
        let tab: RootTab
        switch destination {
        case .startHere:
            briefing.selectStartHere()
            tab = .briefing
        case .news:
            if !briefing.isNewsTierSelected { briefing.selectNewsTier() }
            tab = .briefing
        case .lens(let key):
            briefing.selectLens(key: key)
            tab = .briefing
        case .knowledge:
            tab = .knowledge
        }
        // The open item belongs to the tier it came from; another Briefing
        // tier starts at its list or News on its own.
        if tab == .briefing, destination != previousBriefingDestination {
            briefingPath = []
        }
        // `select` bumps scroll-to-top when the tab is already selected, so a
        // different tier inside Briefing switches without that request.
        if tabCoordinator.selectedTab != tab || isReselection {
            tabCoordinator.select(tab)
        }
    }
}

/// Reports the system's vertical fold (the division reserved region) in the
/// local coordinates of the view it backs. Nil before iOS 27.1 or without a
/// fold, which keeps the equal-halves fallback.
struct OpenDisplayFoldReader: View {
    @Binding var foldFrame: CGRect?

    var body: some View {
        GeometryReader { proxy in
            let fold = Self.fold(in: proxy)
            Color.clear
                .onAppear { foldFrame = fold }
                .onChange(of: fold) { _, newFold in foldFrame = newFold }
        }
    }

    static func fold(in proxy: GeometryProxy) -> CGRect? {
        guard #available(iOS 27.1, *) else { return nil }
        return proxy.reservedRegions(kind: .division, options: .includeInactive).first?.frame
    }
}

private extension View {
    func openDisplayAreaVisibility(_ isVisible: Bool) -> some View {
        opacity(isVisible ? 1 : 0)
            .allowsHitTesting(isVisible)
            .accessibilityHidden(!isVisible)
    }
}
