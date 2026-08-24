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

struct BriefingView: View {
    let viewModel: BriefingViewModel
    let scrollToTopRequest: Int
    private let narrationController: BriefingNarrationController
    /// Sources push onto the tab's navigation stack rather than opening a sheet,
    /// so the reader gets the standard back stack and edge-swipe.
    private let onOpenContent: (ContentDetailRoute) -> Void

    @State private var digViewModel = BriefingDigViewModel(service: LiveBriefingService())
    @State private var playbackService = NarrationPlaybackService.shared
    @State private var activeNarrationChapters: BriefingNarrationChapterSheetItem?
    @State private var chromeCollapse = BriefingChromeCollapseModel()
    @State private var mastheadHeight: CGFloat = 0
    @State private var categoryStripHeight: CGFloat = 0
    @State private var expandedChromeHeight: CGFloat = 0
    @State private var activeAlert: BriefingViewAlert?
    @State private var markingCategoryTitle: String?
    @State private var markAllReadFeedbackTrigger = 0

    init(
        viewModel: BriefingViewModel,
        scrollToTopRequest: Int = 0,
        onOpenContent: @escaping (ContentDetailRoute) -> Void
    ) {
        self.viewModel = viewModel
        self.scrollToTopRequest = scrollToTopRequest
        self.narrationController = viewModel.narrationController
        self.onOpenContent = onOpenContent
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

    /// Whether the category strip participates in the chrome at all — it only
    /// exists while a news category is selected.
    private var showsCategoryStrip: Bool {
        viewModel.isNewsTierSelected && !viewModel.newsLenses.isEmpty
    }

    private var collapsibleChromeHeight: CGFloat {
        mastheadHeight + (showsCategoryStrip ? categoryStripHeight : 0)
    }

    /// Bottom edge of the chrome that remains pinned while reading. A segment
    /// must pass fully above this edge beneath the pills to become read.
    private var readBoundaryY: CGFloat? {
        briefingPinnedReadBoundaryY(
            expandedChromeHeight: expandedChromeHeight,
            collapsibleChromeHeight: collapsibleChromeHeight
        )
    }

    /// The lens whose scroll position drives the chrome; Start Here never
    /// collapses the masthead.
    private var activeCollapseLensKey: String? {
        viewModel.isStartHereSelected ? nil : viewModel.selectedLensKey
    }

    var body: some View {
        Group {
            switch viewModel.state {
            case .idle, .loading:
                LoadingView()
            case .empty:
                emptyState
            case .error(let message):
                ErrorView(message: message) {
                    Task { await viewModel.loadIndexIfNeeded() }
                }
            case .loaded:
                briefingContent
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .screenContainer()
        .topScreenEdgeFade()
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
                .padding(.top, expandedChromeHeight)
            } else {
                TabView(selection: selectedLensBinding) {
                    ForEach(viewModel.pagerLenses, id: \.key) { lens in
                        BriefingLensPageView(
                            lensKey: lens.key,
                            lensTitle: lens.title,
                            renderModel: viewModel.renderModel(for: lens.key),
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
                            onOpenSource: openSource,
                            onOpenDiscussion: openDiscussion,
                            onDig: startDig,
                            onRefresh: { await viewModel.pullToRefresh() },
                            onLoad: { viewModel.loadLensIfNeeded(key: lens.key) },
                            onRetry: { viewModel.retryLens(key: lens.key) },
                            onFirstPassageVisible: {
                                viewModel.noteFirstPassageVisible(for: lens.key)
                            },
                            onScrolledDown: { viewModel.noteScrolledDown(forLens: lens.key) },
                            onMarkSegmentSeen: viewModel.markSegmentSeen,
                            onSetHeaderPinned: { pinned in
                                viewModel.setHeaderPinned(pinned, forLens: lens.key)
                            }
                        )
                        .equatable()
                        .tag(lens.key)
                    }
                }
                .tabViewStyle(.page(indexDisplayMode: .never))
                .accessibilityIdentifier("briefing.lens_pager")
            }

            headerChrome
        }
        .coordinateSpace(name: briefingReadCoordinateSpaceName)
    }

    @ViewBuilder
    private var refreshStatus: some View {
        switch viewModel.refreshPhase {
        case .waitingForVersion:
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text("Refreshing briefing…")
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 6)
            .background(Color.surfaceSecondary)
            .accessibilityIdentifier("briefing.refresh.waiting")
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
    /// masthead and category strip collapse in lockstep with the scroll
    /// offset (via `BriefingCollapsibleChromeSlot`), so content is never
    /// clipped under the chrome before the chrome itself has moved away.
    /// Today's date (not the generation timestamp) keeps the kicker identical
    /// to the Knowledge tab's masthead.
    private var headerChrome: some View {
        // A tap-opened category strip overlays the content at full height
        // while the masthead stays collapsed; it is retired on scroll.
        let stripPinnedOpen = viewModel.isCategoryStripExpanded && viewModel.isMastheadCompact

        return VStack(spacing: 0) {
            BriefingCollapsibleChromeSlot(
                model: chromeCollapse,
                lensKey: activeCollapseLensKey,
                shrink: { [mastheadHeight] in min($0, mastheadHeight) },
                naturalHeight: $mastheadHeight
            ) {
                EditorialMastheadHeader(
                    title: "Briefing",
                    titleAccessibilityIdentifier: "briefing.screen",
                    trailingAccessory: mastheadListenAccessory,
                    accessoryAlignment: .title
                )
            }

            Group {
                if viewModel.firstRun != nil {
                    BriefingFirstRunStrip(
                        viewModel: viewModel,
                        onSelectStartHere: viewModel.selectStartHere,
                        onSelectLens: viewModel.selectLens
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

            if showsCategoryStrip {
                BriefingCollapsibleChromeSlot(
                    model: chromeCollapse,
                    lensKey: activeCollapseLensKey,
                    shrink: { [mastheadHeight, categoryStripHeight] collapse in
                        stripPinnedOpen
                            ? 0
                            : min(max(collapse - mastheadHeight, 0), categoryStripHeight)
                    },
                    naturalHeight: $categoryStripHeight
                ) {
                    BriefingCategoryStrip(
                        viewModel: viewModel,
                        onSelectLens: { key in
                            withAnimation(.smooth(duration: 0.28)) {
                                viewModel.selectLens(key: key)
                            }
                        },
                        onRequestMarkAllRead: presentMarkAllReadPrompt
                    )
                }
            }

            VStack(spacing: 0) {
                if let lensKey = viewModel.selectedLensKey, !viewModel.isStartHereSelected {
                    listenPanel(lensKey: lensKey)
                }
                refreshStatus
            }
        }
        .measureBriefingExpandedChromeHeight(
            model: chromeCollapse,
            lensKey: activeCollapseLensKey,
            mastheadHeight: mastheadHeight,
            categoryStripHeight: showsCategoryStrip ? categoryStripHeight : 0,
            keepsCategoryStripOpen: stripPinnedOpen,
            expandedHeight: $expandedChromeHeight
        )
        .background(Color.surfacePrimary)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(viewModel.isMastheadCompact ? 0.5 : 0))
                .frame(height: 0.5)
        }
        .shadow(color: .black.opacity(viewModel.isMastheadCompact ? 0.12 : 0), radius: 10, y: 5)
        .animation(.easeInOut(duration: 0.2), value: viewModel.isMastheadCompact)
        .animation(.smooth(duration: 0.28), value: viewModel.isCategoryStripExpanded)
        .animation(.smooth(duration: 0.28), value: viewModel.selectedLensKey)
        .zIndex(1)
    }

    private var mastheadListenAccessory: AnyView? {
        viewModel.isStartHereSelected ? nil : viewModel.selectedLensKey.map { lensKey in
            AnyView(listenAccessory(lensKey: lensKey))
        }
    }

    private func listenAccessory(lensKey: String) -> some View {
        BriefingListenButton(
            isPreparing: narrationController.session(for: lensKey).isPreparing,
            isPlaying: narrationController.isPlaying(lensKey: lensKey),
            onToggle: {
                Task { await narrationController.togglePlayback(for: lensKey) }
            }
        )
    }

    /// Expands beneath the pinned strips only while narration for the selected
    /// lens is preparing or active, so the resting chrome stays quiet.
    @ViewBuilder
    private func listenPanel(lensKey: String) -> some View {
        let session = narrationController.session(for: lensKey)
        let isPreparing = session.isPreparing
        let narration = session.manifest
        let chapterIndex = session.selectedChapterIndex
        let target = narrationController.narrationEpisode(for: lensKey)
            .map { NarrationTarget.audioEpisode($0.id) }
        let isActive = isPreparing || (target != nil && target == playbackService.speakingTarget)

        if session.errorMessage != nil || isActive {
            VStack(alignment: .leading, spacing: 8) {
                if let errorMessage = session.errorMessage {
                    Text(errorMessage)
                        .font(.appCaption)
                        .foregroundStyle(Color.statusDestructive)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if isActive {
                    if let narration {
                        BriefingNarrationChapterControls(
                            narration: narration,
                            selectedIndex: chapterIndex,
                            playbackService: playbackService,
                            target: target,
                            isPreparing: isPreparing,
                            onPrevious: {
                                Task {
                                    await narrationController.playChapter(
                                        at: chapterIndex - 1,
                                        for: lensKey
                                    )
                                }
                            },
                            onShowChapters: {
                                activeNarrationChapters = BriefingNarrationChapterSheetItem(
                                    lensKey: lensKey,
                                    episodeGroupID: narration.episodeGroupId
                                )
                                Task {
                                    await narrationController.refresh(for: lensKey)
                                }
                            },
                            onNext: {
                                Task {
                                    await narrationController.playChapter(
                                        at: chapterIndex + 1,
                                        for: lensKey
                                    )
                                }
                            },
                            onTogglePlayback: {
                                Task { await narrationController.togglePlayback(for: lensKey) }
                            }
                        )
                    } else {
                        NarrationPlaybackControlRow(
                            playbackService: playbackService,
                            target: target,
                            isPreparing: isPreparing,
                            onTogglePlayback: {
                                Task { await narrationController.togglePlayback(for: lensKey) }
                            }
                        )
                        .overlay {
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
                        }
                    }
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.bottom, 10)
        }
    }

    private var emptyState: some View {
        BriefingEmptyStateView(
            refreshPhase: viewModel.refreshPhase,
            onRefresh: viewModel.pullToRefresh
        )
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
