import SwiftUI

private struct BriefingMarkAllReadPrompt: Identifiable {
    let lensKey: String
    let title: String
    let unreadCount: Int

    var id: String { lensKey }
}

private enum BriefingViewAlert: Identifiable {
    case markAllRead(BriefingMarkAllReadPrompt)
    case markAllReadFailed(categoryTitle: String, message: String)

    var id: String {
        switch self {
        case .markAllRead(let prompt):
            return "mark-all-read:\(prompt.lensKey)"
        case .markAllReadFailed(let categoryTitle, _):
            return "mark-all-read-failed:\(categoryTitle)"
        }
    }
}

private struct BriefingDeckSource: Identifiable {
    let source: APIBriefingSource
    var id: String { source.sourceKey }
}

private struct BriefingSourceCommand: Identifiable {
    let id = UUID()
    let source: KnowledgeSource
    let action: BriefingSourceAction
}

struct BriefingView: View {
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    @Environment(AttachedChatController.self) private var attachedChat
    @Environment(\.attachedChatHost) private var attachedChatHost
    @Environment(BriefingSavedSources.self) private var savedSources

    let viewModel: BriefingViewModel
    let scrollToTopRequest: Int
    let contentTextSize: DynamicTypeSize
    let layout: BriefingLayout
    /// True while News is hidden under a chat on the open display, so its
    /// passages are not marked read unseen.
    let isReadTrackingSuspended: Bool
    private let narrationController: BriefingNarrationController
    /// Sources push onto the tab's navigation stack rather than opening a sheet,
    /// so the reader gets the standard back stack and edge-swipe.
    private let onOpenContent: (ContentDetailRoute) -> Void
    private let onSourceMutation: () -> Void

    @State private var sourceActions: BriefingSourceActionViewModel?
    @State private var pendingSourceCommand: BriefingSourceCommand?
    @State private var pendingDeckSource: BriefingDeckSource?
    @State private var deckSource: BriefingDeckSource?
    @State private var pendingDeckReader: LearningDeckReaderDestination?
    @State private var deckReader: LearningDeckReaderDestination?
    @State private var digViewModel: BriefingDigViewModel
    @State private var playbackService: NarrationPlaybackService
    @State private var activeNarrationChapters: BriefingNarrationChapterSheetItem?
    @State private var chromeCollapse = BriefingChromeCollapseModel()
    /// The lens whose collapse the header shows. Follows the selection inside
    /// an animation, so switching pages slides the header between the two
    /// lenses' positions instead of jumping.
    @State private var chromeLensKey: String?
    @State private var mastheadHeight: CGFloat = 0
    @State private var expandedChromeHeight: CGFloat = 0
    @State private var pagerScreenEdgeInsets = EdgeInsets()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(RootTabBarMinimizer.self) private var tabBarMinimizer: RootTabBarMinimizer?
    @State private var listenPanelFullHeight: CGFloat = 0
    @State private var listenPanelMinimizedHeight: CGFloat = 0
    /// A tap on the minimized player reopens the full card while scrolled;
    /// the next scroll-down retires it, like the tap-opened category strip.
    @State private var listenPanelPinnedOpen = false
    @State private var activeAlert: BriefingViewAlert?
    @State private var markingCategoryTitle: String?
    @State private var markAllReadFeedbackTrigger = 0
    /// The column never collapses chrome; it gets its own inert model.
    @State private var columnCollapse = BriefingChromeCollapseModel()
    /// Read passage pauses while the column resizes (opening or closing a
    /// foldable, or a story or chat opening beside News), because reflowed
    /// passages would otherwise cross the boundary without being read.
    @State private var isColumnResizing = false
    @State private var columnWidth: CGFloat = 0
    @State private var columnResizeGeneration = 0
    /// The source News last anchored to, so the first-appearance anchor
    /// does not repeat one already applied.
    @State private var anchoredSourceKey: String?
    @State private var anchorRequest: BriefingScrollAnchorRequest?
    /// Bumped to (re)run the delayed anchor task; a newer request cancels an
    /// older one that has not fired yet.
    @State private var anchorNonce = 0
    @State private var anchorTargetSegmentID: Int?

    init(
        viewModel: BriefingViewModel,
        playbackService: NarrationPlaybackService,
        scrollToTopRequest: Int = 0,
        contentTextSize: DynamicTypeSize,
        layout: BriefingLayout = .pager,
        isReadTrackingSuspended: Bool = false,
        onOpenContent: @escaping (ContentDetailRoute) -> Void,
        onSourceMutation: @escaping () -> Void = {}
    ) {
        self.viewModel = viewModel
        self.scrollToTopRequest = scrollToTopRequest
        self.contentTextSize = contentTextSize
        self.layout = layout
        self.isReadTrackingSuspended = isReadTrackingSuspended
        self.narrationController = viewModel.narrationController
        self.onOpenContent = onOpenContent
        self.onSourceMutation = onSourceMutation
        self._digViewModel = State(
            initialValue: BriefingDigViewModel(service: viewModel.service)
        )
        self._playbackService = State(initialValue: playbackService)
    }

    private var digSheetPresented: Binding<Bool> {
        Binding(
            get: { !digViewModel.isIdle },
            set: { isPresented in
                if !isPresented {
                    digViewModel.clear()
                }
            }
        )
    }

    private var selectedLensBinding: Binding<String> {
        Binding(
            get: { viewModel.selectedLensKey ?? viewModel.orderedLenses.first?.key ?? "" },
            set: { viewModel.selectLens(key: $0) }
        )
    }

    /// First-run navigation already includes every category. The separate
    /// category strip belongs only beneath the normal tier strip.
    private var showsCategoryStrip: Bool {
        viewModel.firstRun == nil && viewModel.isNewsTierSelected && !viewModel.newsLenses.isEmpty
    }

    /// Only the masthead collapses with the offset; the tier and category
    /// strips and the player then ride the text off screen and return on any
    /// upward scroll (`BriefingChromeCollapseModel.trackPinnedChrome`).
    private var collapsibleChromeHeight: CGFloat {
        mastheadHeight
    }

    private var isListenPanelMinimized: Bool {
        viewModel.isMastheadCompact && !listenPanelPinnedOpen
    }

    /// Height the minimized player gives back while the masthead is compact.
    /// Derived, not measured, so it changes in the same pass as the shape.
    private var listenPanelShrink: CGFloat {
        guard isListenPanelMinimized, listenPanelFullHeight > 0, listenPanelMinimizedHeight > 0 else {
            return 0
        }
        return max(listenPanelFullHeight - listenPanelMinimizedHeight, 0)
    }

    /// Bottom edge of the chrome that remains pinned while reading. A segment
    /// must pass fully above this edge beneath the pills to become read.
    private var readBoundaryY: CGFloat? {
        briefingPinnedReadBoundaryY(
            expandedChromeHeight: expandedChromeHeight - listenPanelShrink,
            collapsibleChromeHeight: collapsibleChromeHeight
        )
    }

    /// The lens whose scroll position drives the chrome; Start Here never
    /// collapses the masthead.
    private var activeCollapseLensKey: String? {
        viewModel.isStartHereSelected ? nil : viewModel.selectedLensKey
    }

    /// Slide that takes the pinned chrome fully off screen. Owned here so it
    /// follows the header's height even when nothing scrolls.
    private var pinnedHideDistance: CGFloat {
        briefingPinnedChromeHideDistance(
            screenTop: pagerScreenEdgeInsets.top,
            pinnedHeight: readBoundaryY ?? 0
        )
    }

    var body: some View {
        Group {
            switch viewModel.state {
            case .idle, .loading:
                BriefingLoadingView()
            case .empty:
                emptyState
            case .error(let message):
                ErrorView(message: message) {
                    Task { await viewModel.loadIndexIfNeeded() }
                }
                .accessibilityIdentifier("briefing.blocking_error")
            case .loaded:
                switch layout {
                case .pager:
                    briefingContent
                case .column(let columnLayout):
                    columnContent(columnLayout)
                }
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .screenContainer()
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if let markingCategoryTitle {
                Color.black.opacity(0.15)
                    .ignoresSafeArea()

                ProgressView("Marking \(markingCategoryTitle) as read")
                    .padding(16)
                    .background(Color.surfacePrimary)
                    .clipShape(
                        RoundedRectangle(
                            cornerRadius: CornerRadius.control,
                            style: .continuous
                        )
                    )
                    .accessibilityIdentifier("briefing.category.mark_all.progress")
            }
        }
        .alert(item: $activeAlert) { alert in
            switch alert {
            case .markAllRead(let prompt):
                Alert(
                    title: Text("Mark all in “\(prompt.title)” as read?"),
                    message: Text(markAllReadMessage(for: prompt)),
                    primaryButton: .destructive(Text("Mark All as Read")) {
                        markAllSourcesRead(in: prompt)
                    },
                    secondaryButton: .cancel()
                )
            case .markAllReadFailed(let categoryTitle, let message):
                Alert(
                    title: Text("Couldn’t mark “\(categoryTitle)” as read"),
                    message: Text(message),
                    dismissButton: .default(Text("OK"))
                )
            }
        }
        .sensoryFeedback(.success, trigger: markAllReadFeedbackTrigger)
        .task(id: pendingSourceCommand?.id) {
            await performPendingSourceCommand()
        }
        .sheet(item: $deckSource, onDismiss: {
            deckReader = pendingDeckReader
            pendingDeckReader = nil
        }) { item in
            if let sourceActions {
                BriefingSourceDeckCreateSheet(
                    source: item.source,
                    viewModel: sourceActions,
                    focusRecorder: dependencyFactory.makeLearningDeckFocusRecorder(),
                    onCreated: { deck in
                        onSourceMutation()
                        pendingDeckReader = LearningDeckReaderDestination(deck: deck, url: nil)
                    }
                )
            }
        }
        .fullScreenCover(item: $deckReader) { destination in
            LearningDeckReaderView(
                deck: destination.deck,
                viewerURL: destination.url,
                onClose: { deckReader = nil }
            )
            .dynamicTypeSize(contentTextSize)
            .ignoresSafeArea(.container)
        }
        .sheet(item: $activeNarrationChapters) { item in
            if let narration = narrationController.narration(for: item.lensKey) {
                BriefingNarrationChapterSheet(
                    narration: narration,
                    selectedIndex: narrationController.narrationChapterIndex(for: item.lensKey),
                    isPreparing: narrationController.session(for: item.lensKey).isPreparing,
                    onSelect: { chapterIndex in
                        Task {
                            await narrationController.playChapter(
                                at: chapterIndex,
                                for: item.lensKey
                            )
                        }
                    }
                )
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
            }
        }
        .sheet(isPresented: digSheetPresented) {
            BriefingDigSheet(viewModel: digViewModel)
                .presentationDetents([.fraction(0.75), .large])
                .presentationContentInteraction(.resizes)
                .presentationDragIndicator(.visible)
        }
    }

    /// The chrome overlays the pager instead of stacking above it: the pager's
    /// frame never changes while the chrome collapses, so scrolling stays
    /// smooth, and pages inset their content by the expanded chrome height so
    /// text is never occluded before the chrome has moved out of the way.
    private var briefingContent: some View {
        ZStack(alignment: .top) {
            if viewModel.isStartHereSelected, let firstRun = viewModel.firstRun {
                BriefingStartHereView(
                    progress: firstRun,
                    scrollToTopRequest: scrollToTopRequest,
                    onRefresh: viewModel.refreshIndex
                )
                .dynamicTypeSize(contentTextSize)
                .padding(.top, expandedChromeHeight)
            } else {
                TabView(selection: selectedLensBinding) {
                    ForEach(viewModel.pagerLenses, id: \.key) { lens in
                        BriefingLensPageView(
                            lensKey: lens.key,
                            lensTitle: lens.title,
                            renderModel: viewModel.renderModel(for: lens.key),
                            firstRunProgress: viewModel.firstRun?.tiers.first { $0.tier == lens.tier.rawValue },
                            isReadTrackingEnabled: viewModel.isActive
                                && viewModel.selectedLensKey == lens.key,
                            readBoundaryY: readBoundaryY,
                            documentGeneration: viewModel.documentGeneration(for: lens.key),
                            scrollToTopRequest: scrollToTopRequest,
                            shouldScrollToTop: viewModel.selectedLensKey == lens.key,
                            error: viewModel.lensErrors[lens.key],
                            continuationError: viewModel.lensContinuationErrors[lens.key],
                            isLoadingContinuation: viewModel.lensContinuationLoadingKeys.contains(lens.key),
                            chromeCollapse: chromeCollapse,
                            collapsibleChromeHeight: collapsibleChromeHeight,
                            topContentInset: expandedChromeHeight,
                            screenEdgeInsets: pagerScreenEdgeInsets,
                            onOpenSource: openSource,
                            onSourceAction: handleSourceAction,
                            savedSources: savedSources,
                            onOpenDiscussion: openDiscussion,
                            onDig: startDig,
                            onRefresh: { await viewModel.pullToRefresh() },
                            onLoad: { viewModel.loadLensIfNeeded(key: lens.key) },
                            onRetry: { viewModel.retryLens(key: lens.key) },
                            onFirstPassageVisible: {
                                viewModel.noteFirstPassageVisible(for: lens.key)
                            },
                            onScrolledDown: {
                                listenPanelPinnedOpen = false
                            },
                            onMarkSegmentSeen: viewModel.markSegmentSeen,
                            onSetHeaderPinned: { pinned in
                                viewModel.setHeaderPinned(pinned, forLens: lens.key)
                            }
                        )
                        .equatable()
                        .dynamicTypeSize(contentTextSize)
                        .tag(lens.key)
                    }
                }
                .tabViewStyle(.page(indexDisplayMode: .never))
                // The page view clips its pages to its own frame, so a pager
                // inside the safe area cut text off in hard lines under the
                // status bar and above the home indicator.
                .ignoresSafeArea(.container, edges: .vertical)
                .accessibilityIdentifier("briefing.lens_pager")
            }

            if !viewModel.isStartHereSelected {
                BriefingStatusBarFade(height: pagerScreenEdgeInsets.top)
            }

            headerChrome
        }
        .onGeometryChange(for: EdgeInsets.self) { proxy in
            proxy.safeAreaInsets
        } action: { _, insets in
            pagerScreenEdgeInsets = insets
        }
        // Empty, error and Start Here pages never report a scroll, so a bar
        // condensed on the previous page would otherwise stay condensed.
        .onChange(of: viewModel.selectedLensKey) { _, key in
            tabBarMinimizer?.expand()
            // A page reached by swipe or tap starts with its pills in view.
            if let key { chromeCollapse.revealPinnedChrome(forLens: key, reduceMotion: reduceMotion) }
        }
        .onChange(of: viewModel.isStartHereSelected) { _, _ in
            tabBarMinimizer?.expand()
        }
        .onChange(of: activeCollapseLensKey, initial: true) { _, key in
            withAnimation(headerAnimation) { chromeLensKey = key }
        }
        .onChange(of: pinnedHideDistance, initial: true) { _, distance in
            chromeCollapse.setPinnedHideDistance(distance)
        }
    }

    @ViewBuilder
    private var refreshStatus: some View {
        switch viewModel.refreshPhase {
        case .waitingForCompletion:
            BriefingRefreshWaitingView()
        case .failed(let message):
            HStack(spacing: 10) {
                Text(message)
                    .font(.appCaption)
                    .foregroundStyle(Color.statusDestructive)
                    .lineLimit(2)
                Spacer(minLength: 8)
                Button("Try Again") {
                    Task { await viewModel.pullToRefresh() }
                }
                .font(.appCaption.weight(.semibold))
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 7)
            .background(Color.surfaceSecondary)
            .accessibilityIdentifier("briefing.refresh.failed")
        case .idle, .requesting:
            EmptyView()
        }
    }

    /// Everything above the pager — masthead, tier strip, category strip, and
    /// the playback panel — stays pinned while pages swipe underneath. The
    /// whole header moves up in lockstep with the scroll offset, masthead then
    /// pills in one continuous motion (`briefingHeaderScrollOffset`), so
    /// content is never clipped under the chrome before the chrome itself has
    /// moved away.
    /// Today's date (not the generation timestamp) keeps the kicker identical
    /// to the Knowledge tab's masthead.
    private var headerChrome: some View {
        VStack(spacing: 0) {
            EditorialMastheadHeader(
                title: "Briefing",
                titleAccessibilityIdentifier: "briefing.screen",
                trailingAccessory: mastheadListenAccessory
            )
            .briefingCollapsingMasthead(
                model: chromeCollapse,
                lensKey: chromeLensKey,
                naturalHeight: $mastheadHeight
            )

            pinnedChrome
        }
        .measureBriefingExpandedChromeHeight(
            additionalShrink: listenPanelShrink,
            expandedHeight: $expandedChromeHeight
        )
        .onChange(of: viewModel.isMastheadCompact) { _, isCompact in
            if !isCompact {
                listenPanelPinnedOpen = false
            }
        }
        .briefingHeaderScrollOffset(
            model: chromeCollapse,
            lensKey: chromeLensKey,
            mastheadHeight: mastheadHeight
        )
        // No header-wide animation: an animated update keeps a removed view on
        // screen until it finishes, so the category strip lingered over text
        // that had already moved up. Only the pill tints and player animate.
        .zIndex(1)
    }

    /// The strips and player that stay pinned once the masthead has collapsed.
    private var pinnedChrome: some View {
        VStack(spacing: 0) {
            Group {
                if viewModel.firstRun != nil {
                    BriefingFirstRunStrip(
                        viewModel: viewModel,
                        onSelectStartHere: viewModel.selectStartHere,
                        onSelectLens: viewModel.selectLens,
                        onRequestMarkAllRead: presentMarkAllReadPrompt
                    )
                } else {
                    BriefingTierStrip(
                        viewModel: viewModel,
                        onSelectNews: { viewModel.selectNewsTier() },
                        onSelectLens: { key in viewModel.selectLens(key: key) },
                        onRequestMarkAllRead: presentMarkAllReadPrompt
                    )
                }
            }
            .animation(headerAnimation, value: viewModel.selectedLensKey)

            if showsCategoryStrip {
                // Leaves instantly: the pages re-inset in the same frame, so a
                // fade-out overlapped the text that had already moved up beneath
                // it. Arrival can fade because the space is reserved first.
                BriefingCategoryStrip(
                    viewModel: viewModel,
                    onSelectLens: { key in
                        withAnimation(.smooth(duration: 0.28)) {
                            viewModel.selectLens(key: key)
                        }
                    },
                    onRequestMarkAllRead: presentMarkAllReadPrompt
                )
                .animation(headerAnimation, value: viewModel.selectedLensKey)
                .transition(.asymmetric(
                    insertion: .opacity.animation(headerAnimation),
                    removal: .identity
                ))
            }

            VStack(spacing: 0) {
                if let lensKey = viewModel.selectedLensKey, !viewModel.isStartHereSelected {
                    listenPanel(lensKey: lensKey)
                }
                refreshStatus
            }
            .animation(headerAnimation, value: viewModel.selectedLensKey)
        }
        .background(alignment: .top) {
            BriefingPinnedChromeBacking(
                model: chromeCollapse,
                lensKey: chromeLensKey,
                mastheadHeight: mastheadHeight
            )
        }
    }

    private var headerAnimation: Animation {
        AppMotion.respectingReduceMotion(reduceMotion, .smooth(duration: 0.28))
    }

    private var mastheadListenAccessory: AnyView? {
        viewModel.isStartHereSelected ? nil : viewModel.selectedLensKey.map { lensKey in
            AnyView(listenAccessory(lensKey: lensKey))
        }
    }

    private func listenAccessory(lensKey: String) -> some View {
        return BriefingListenButton(
            isPreparing: narrationController.session(for: lensKey).isPreparing,
            isPlaying: narrationController.isPlaying(lensKey: lensKey),
            onToggle: {
                Task { await narrationController.togglePlayback(for: lensKey) }
            }
        )
    }

    /// Expands beneath the pinned strips only while narration for the selected
    /// lens is preparing or active, so the resting chrome stays quiet. Once
    /// the masthead collapses the card minimizes to a single line.
    @ViewBuilder
    private func listenPanel(lensKey: String) -> some View {
        let session = narrationController.session(for: lensKey)
        let target = narrationController.narrationEpisode(for: lensKey)
            .map { NarrationTarget.audioEpisode($0.id) }
        let snapshot = NarrationPlaybackSnapshot(
            playbackService: playbackService,
            target: target,
            isPreparing: session.isPreparing
        )
        let isActive = session.isPreparing
            || (target != nil && target == playbackService.speakingTarget)

        if session.errorMessage != nil || isActive {
            VStack(alignment: .leading, spacing: 8) {
                if let errorMessage = session.errorMessage {
                    Text(errorMessage)
                        .font(.appCaption)
                        .foregroundStyle(Color.statusDestructive)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if isActive {
                    BriefingNowPlayingPanel(
                        lensTitle: viewModel.orderedLenses.first { $0.key == lensKey }?.title ?? "Briefing",
                        narration: session.manifest,
                        selectedIndex: session.selectedChapterIndex,
                        snapshot: snapshot,
                        isMinimized: isListenPanelMinimized,
                        onTogglePlayback: {
                            Task { await narrationController.togglePlayback(for: lensKey) }
                        },
                        onPrevious: {
                            Task {
                                await narrationController.playChapter(
                                    at: session.selectedChapterIndex - 1,
                                    for: lensKey
                                )
                            }
                        },
                        onNext: {
                            Task {
                                await narrationController.playChapter(
                                    at: session.selectedChapterIndex + 1,
                                    for: lensKey
                                )
                            }
                        },
                        onShowChapters: {
                            guard let narration = session.manifest else { return }
                            activeNarrationChapters = BriefingNarrationChapterSheetItem(
                                lensKey: lensKey,
                                episodeGroupID: narration.episodeGroupId
                            )
                            Task {
                                await narrationController.refresh(for: lensKey)
                            }
                        },
                        onSeek: { progress in
                            guard let target else { return }
                            playbackService.seek(to: progress, for: target)
                        },
                        onSetPlaybackRate: { rate in
                            playbackService.setPlaybackRate(rate)
                        },
                        onExpand: { listenPanelPinnedOpen = true },
                        onDismiss: { narrationController.stopPlayback(for: lensKey) },
                        fullHeight: $listenPanelFullHeight,
                        minimizedHeight: $listenPanelMinimizedHeight
                    )
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.bottom, 10)
            .onDisappear {
                listenPanelFullHeight = 0
                listenPanelMinimizedHeight = 0
            }
        }
    }

    // MARK: Open-display column

    private var columnTitle: String {
        if viewModel.isStartHereSelected { return "Welcome" }
        if viewModel.isNewsTierSelected { return "News" }
        return viewModel.selectedLensSummary?.title ?? "Briefing"
    }

    private var showsColumnCategoryStrip: Bool {
        !viewModel.isStartHereSelected && viewModel.isNewsTierSelected && !viewModel.newsLenses.isEmpty
    }

    private func columnContent(_ columnLayout: BriefingColumnLayout) -> some View {
        let selectedSourceKey = columnLayout.selectedSourceKey
        let presentsRows = columnLayout.presentsCardsAsRows
        // The header lines up with the centred reading column; the category
        // strip starts there too but still scrolls the full width.
        let readingInset = columnLayout.readingWidth.map { max((columnWidth - $0) / 2, 0) } ?? 0
        return VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("BRIEFING")
                        .font(.appCaption2)
                        .tracking(1)
                        .foregroundStyle(Color.onSurfaceTertiary)
                    Text(columnTitle)
                        .font(.appTitle2)
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(1)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier("briefing.screen")
                }
                .padding(.horizontal, Spacing.appHorizontalMargin + readingInset)

                if showsColumnCategoryStrip {
                    BriefingCategoryStrip(
                        viewModel: viewModel,
                        onSelectLens: { key in viewModel.selectLens(key: key) },
                        onRequestMarkAllRead: presentMarkAllReadPrompt
                    )
                    .safeAreaPadding(.leading, readingInset)
                }
            }
            .padding(.top, 8)
            .padding(.bottom, showsColumnCategoryStrip ? 0 : 10)

            Divider()

            columnLensPage(columnLayout)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { oldWidth, newWidth in
            columnWidth = newWidth
            guard oldWidth > 0, abs(oldWidth - newWidth) > 0.5 else { return }
            isColumnResizing = true
            columnResizeGeneration += 1
        }
        .task(id: columnResizeGeneration) {
            guard isColumnResizing else { return }
            try? await Task.sleep(for: .milliseconds(450))
            guard !Task.isCancelled else { return }
            isColumnResizing = false
        }
        .onChange(of: selectedSourceKey) { oldKey, newKey in
            // News reflows when a story opens or closes; keep the passage
            // the reader opened (or came back from) in view.
            guard !presentsRows else { return }
            // Picking another story while one is open: News does not reflow,
            // and the tapped passage is already in view.
            if oldKey != nil, let newKey {
                anchoredSourceKey = newKey
                return
            }
            guard let key = newKey ?? oldKey else { return }
            requestAnchor(to: key)
        }
        // A story already open when News appears (after unfolding, or once
        // the lens document loads) is anchored too.
        .task(id: initialAnchorKey(selectedSourceKey: selectedSourceKey, presentsRows: presentsRows)) {
            guard !presentsRows, let key = selectedSourceKey, anchoredSourceKey != key else { return }
            requestAnchor(to: key)
        }
        .task(id: anchorNonce) {
            guard let target = anchorTargetSegmentID else { return }
            // After the reflow's layout pass.
            try? await Task.sleep(for: .milliseconds(60))
            guard !Task.isCancelled else { return }
            anchorRequest = BriefingScrollAnchorRequest(segmentID: target, generation: anchorNonce)
        }
    }

    private func requestAnchor(to sourceKey: String) {
        guard let segmentID = segmentID(containing: sourceKey) else { return }
        anchoredSourceKey = sourceKey
        anchorTargetSegmentID = segmentID
        anchorNonce += 1
    }

    private func initialAnchorKey(selectedSourceKey: String?, presentsRows: Bool) -> String {
        let generation = viewModel.selectedLensKey.map { viewModel.documentGeneration(for: $0) } ?? -1
        return "\(presentsRows)|\(selectedSourceKey ?? "")|\(viewModel.selectedLensKey ?? "")|\(generation)"
    }

    private func segmentID(containing sourceKey: String) -> Int? {
        guard let lensKey = viewModel.selectedLensKey else { return nil }
        return viewModel.renderModel(for: lensKey)?.segments
            .first { $0.segment.sourceKeys.contains(sourceKey) }?
            .segment.id
    }

    @ViewBuilder
    private func columnLensPage(_ columnLayout: BriefingColumnLayout) -> some View {
        if viewModel.isStartHereSelected, let firstRun = viewModel.firstRun {
            BriefingStartHereView(
                progress: firstRun,
                scrollToTopRequest: scrollToTopRequest,
                onRefresh: viewModel.refreshIndex
            )
            .dynamicTypeSize(contentTextSize)
        } else if let lens = viewModel.pagerLenses.first(where: { $0.key == viewModel.selectedLensKey }) {
            BriefingLensPageView(
                lensKey: lens.key,
                lensTitle: lens.title,
                renderModel: viewModel.renderModel(for: lens.key),
                firstRunProgress: viewModel.firstRun?.tiers.first { $0.tier == lens.tier.rawValue },
                isReadTrackingEnabled: viewModel.isActive && !isColumnResizing && !isReadTrackingSuspended,
                // The column has no pinned chrome: a passage is read once it
                // has scrolled fully above the top of the page.
                readBoundaryY: 0,
                documentGeneration: viewModel.documentGeneration(for: lens.key),
                scrollToTopRequest: scrollToTopRequest,
                shouldScrollToTop: true,
                error: viewModel.lensErrors[lens.key],
                continuationError: viewModel.lensContinuationErrors[lens.key],
                isLoadingContinuation: viewModel.lensContinuationLoadingKeys.contains(lens.key),
                chromeCollapse: columnCollapse,
                collapsibleChromeHeight: 0,
                topContentInset: 0,
                onOpenSource: openSource,
                onSourceAction: handleSourceAction,
                savedSources: savedSources,
                onOpenDiscussion: openDiscussion,
                onDig: startDig,
                onRefresh: { await viewModel.pullToRefresh() },
                onLoad: { viewModel.loadLensIfNeeded(key: lens.key) },
                onRetry: { viewModel.retryLens(key: lens.key) },
                onFirstPassageVisible: {
                    viewModel.noteFirstPassageVisible(for: lens.key)
                },
                onScrolledDown: {},
                onMarkSegmentSeen: viewModel.markSegmentSeen,
                onSetHeaderPinned: { _ in },
                columnLayout: columnLayout,
                anchorRequest: anchorRequest
            )
            .equatable()
            .dynamicTypeSize(contentTextSize)
            .id(lens.key)
        } else {
            Color.clear
        }
    }

    private var emptyState: some View {
        BriefingEmptyStateView(
            refreshPhase: viewModel.refreshPhase,
            onRefresh: viewModel.pullToRefresh
        )
    }

    private func handleSourceAction(_ sourceKey: String, action: BriefingSourceAction) {
        if action == .addToKnowledge {
            saveSource(sourceKey)
            return
        }
        guard pendingSourceCommand == nil, deckSource == nil,
              let source = viewModel.source(for: sourceKey),
              let identity = BriefingSourceActionViewModel.knowledgeSource(for: source)
        else { return }
        if sourceActions == nil {
            sourceActions = dependencyFactory.makeBriefingSourceActionViewModel()
        }
        pendingSourceCommand = BriefingSourceCommand(source: identity, action: action)
        // Capture the form source now; presenting it waits for the existing-deck lookup.
        if action == .createDeck {
            pendingDeckReader = nil
            pendingDeckSource = BriefingDeckSource(source: source)
        }
    }

    private func performPendingSourceCommand() async {
        guard let command = pendingSourceCommand, let sourceActions else { return }
        defer {
            if pendingSourceCommand?.id == command.id {
                pendingSourceCommand = nil
                pendingDeckSource = nil
            }
        }
        switch command.action {
        case .addToKnowledge:
            // Saves run per source through `saveSource`, never as a queued command.
            return
        case .chat:
            if let route = await sourceActions.startChat(source: command.source), !Task.isCancelled {
                onSourceMutation()
                attachedChat.present(route, host: attachedChatHost)
            }
        case .createDeck:
            let existing = await sourceActions.existingDeck(
                source: command.source, sourceURL: pendingDeckSource?.source.url
            )
            guard !Task.isCancelled else { return }
            if let existing {
                deckReader = LearningDeckReaderDestination(deck: existing, url: nil)
            } else if sourceActions.errorMessage == nil {
                deckSource = pendingDeckSource
            }
        }
        if !Task.isCancelled, let message = sourceActions.errorMessage {
            dependencyFactory.toastService.showError(message)
        }
    }

    /// Saves independently per source, so saving one card never drops a save
    /// started from another card or a passage link.
    private func saveSource(_ sourceKey: String) {
        let savedSources = savedSources
        guard !savedSources.isPending(sourceKey),
              let source = viewModel.source(for: sourceKey),
              let identity = BriefingSourceActionViewModel.knowledgeSource(for: source)
        else { return }
        let toastService = dependencyFactory.toastService
        let onSourceMutation = onSourceMutation
        Task {
            let outcome = await savedSources.save(sourceKey: sourceKey, source: identity)
            guard !savedSources.isDetached, !Task.isCancelled else { return }
            switch outcome {
            case .saved(let message):
                toastService.showSuccess(message)
                onSourceMutation()
            case .failed(let message):
                toastService.showError(message)
            case .ignored:
                break
            }
        }
    }

    private func openSource(_ sourceKey: String) {
        guard let source = viewModel.source(for: sourceKey) else { return }
        onOpenContent(contentRoute(for: source))
    }

    private func openDiscussion(_ source: APIBriefingSource) {
        onOpenContent(contentRoute(for: source, initialScrollTarget: .comments))
    }

    /// `APIBriefingSource.contentType` is optional, but `kind` is the same
    /// vocabulary and is already what groups sibling sources for the carousel,
    /// so it is the fallback rather than `.unknown`.
    private func contentRoute(
        for source: APIBriefingSource,
        initialScrollTarget: ContentDetailScrollTarget? = nil
    ) -> ContentDetailRoute {
        ContentDetailRoute(
            contentId: source.id,
            contentType: source.contentType ?? APIContentType(rawValue: source.kind),
            allContentIds: contentIdsForCurrentLens(matching: source.kind),
            navigationSurface: .briefing,
            initialScrollTarget: initialScrollTarget
        )
    }

    private func startDig(fragment: String, passageContext: String) {
        digViewModel.dig(fragment: fragment, passageContext: passageContext)
    }

    private func presentMarkAllReadPrompt(for lens: APIBriefingLensSummary) {
        guard markingCategoryTitle == nil, lens.unreadSourceCount > 0 else { return }
        activeAlert = .markAllRead(
            BriefingMarkAllReadPrompt(
                lensKey: lens.key,
                title: lens.title,
                unreadCount: lens.unreadSourceCount
            )
        )
    }

    private func markAllReadMessage(for prompt: BriefingMarkAllReadPrompt) -> String {
        let noun = prompt.unreadCount == 1 ? "item" : "items"
        return "Marks all \(prompt.unreadCount) unread \(noun) in this category."
    }

    private func markAllSourcesRead(in prompt: BriefingMarkAllReadPrompt) {
        guard markingCategoryTitle == nil else { return }
        markingCategoryTitle = prompt.title
        Task {
            defer { markingCategoryTitle = nil }
            do {
                try await viewModel.markAllSourcesRead(in: prompt.lensKey)
                markAllReadFeedbackTrigger += 1
            } catch {
                activeAlert = .markAllReadFailed(
                    categoryTitle: prompt.title,
                    message: error.localizedDescription
                )
            }
        }
    }

    /// Ids of same-kind sources in the current lens, so the detail screen can
    /// swipe between the lens's stories without mixing content and news ids.
    private func contentIdsForCurrentLens(matching kind: String) -> [Int] {
        guard let selectedLens = viewModel.selectedLens else { return [] }
        return selectedLens.sources
            .filter { $0.kind == kind }
            .map(\.id)
    }

}

struct BriefingLoadingView: View {
    var body: some View {
        VStack(spacing: 0) {
            EditorialMastheadHeader(
                title: "Briefing",
                titleAccessibilityIdentifier: "briefing.screen"
            )

            BuddyLoadingView(message: "Preparing your briefing")
                .accessibilityIdentifier("briefing.loading")
        }
        .background(Color.surfacePrimary)
    }
}

private struct BriefingRefreshWaitingView: View {
    var body: some View {
        HStack(spacing: 8) {
            BuddyLoadingIndicator(size: 22)
                .accessibilityHidden(true)

            Text("Refreshing briefing…")
                .font(.appCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .background(Color.surfaceSecondary)
        .accessibilityIdentifier("briefing.refresh.waiting")
    }
}
