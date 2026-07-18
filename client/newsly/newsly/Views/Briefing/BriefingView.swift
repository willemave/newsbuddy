import os.log
import SwiftUI

private let briefingNarrationLogger = Logger(
    subsystem: "com.newsly",
    category: "BriefingNarration"
)

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
    @ObservedObject var viewModel: BriefingViewModel

    @Environment(\.dynamicTypeSize) private var contentTextSize
    @StateObject private var digViewModel = BriefingDigViewModel(service: LiveBriefingService())
    @State private var playbackService = NarrationPlaybackService.shared
    @State private var activeSource: BriefingSourceSheetItem?
    @State private var preparingNarrationLensKeys: Set<String> = []
    @State private var narrationError: String?
    @State private var chromeCollapse = BriefingChromeCollapseModel()
    @State private var mastheadHeight: CGFloat = 0
    @State private var categoryStripHeight: CGFloat = 0
    @State private var expandedChromeHeight: CGFloat = 0
    @State private var activeAlert: BriefingViewAlert?
    @State private var markingCategoryTitle: String?
    @State private var markAllReadFeedbackTrigger = 0

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

    /// Bottom edge of the chrome that remains pinned while reading. Segment
    /// midpoints cross this edge beneath the pills, not the pager's raw top.
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
        .accessibilityIdentifier("briefing.screen")
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
        .sheet(item: $activeSource) { item in
            BriefingSourceSheet(
                item: item,
                contentIds: contentIdsForCurrentLens(matching: item.source.kind)
            )
            .dynamicTypeSize(contentTextSize)
            .presentationDetents([.fraction(0.75), .large])
            .presentationContentInteraction(.resizes)
            .presentationDragIndicator(.visible)
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
                BriefingStartHereView(progress: firstRun)
                    .padding(.top, expandedChromeHeight)
            } else {
                TabView(selection: selectedLensBinding) {
                    ForEach(viewModel.pagerLenses, id: \.key) { lens in
                        BriefingLensPageView(
                            lensKey: lens.key,
                            renderModel: viewModel.renderModel(for: lens.key),
                            isReadTrackingEnabled: viewModel.isActive
                                && viewModel.selectedLensKey == lens.key,
                            readBoundaryY: readBoundaryY,
                            documentGeneration: viewModel.documentGeneration(for: lens.key),
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
                .id(viewModel.isNewsTierSelected ? "tier:news" : "lens:\(viewModel.selectedLensKey ?? "")")
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
                Button("Retry") {
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
                        onSelectLens: { key in viewModel.selectLens(key: key) }
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
            isPreparing: preparingNarrationLensKeys.contains(lensKey),
            isPlaying: isNarrationPlaying(lensKey: lensKey),
            onToggle: {
                Task { await toggleNarration(lensKey: lensKey) }
            }
        )
    }

    /// Expands beneath the pinned strips only while narration for the selected
    /// lens is preparing or active, so the resting chrome stays quiet.
    @ViewBuilder
    private func listenPanel(lensKey: String) -> some View {
        let isPreparing = preparingNarrationLensKeys.contains(lensKey)
        let target = viewModel.narrationEpisode(for: lensKey)
            .map { NarrationTarget.audioEpisode($0.id) }
        let isActive = isPreparing || (target != nil && target == playbackService.speakingTarget)

        if narrationError != nil || isActive {
            VStack(alignment: .leading, spacing: 8) {
                if let narrationError {
                    Text(narrationError)
                        .font(.appCaption)
                        .foregroundStyle(Color.statusDestructive)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if isActive {
                    NarrationPlaybackControlRow(
                        playbackService: playbackService,
                        target: target,
                        isPreparing: isPreparing,
                        onTogglePlayback: {
                            Task { await toggleNarration(lensKey: lensKey) }
                        }
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
                    }
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.bottom, 10)
        }
    }

    private func isNarrationPlaying(lensKey: String) -> Bool {
        guard let episode = viewModel.narrationEpisode(for: lensKey) else { return false }
        return playbackService.speakingTarget == .audioEpisode(episode.id)
            && playbackService.isSpeaking
    }

    private var emptyState: some View {
        BriefingEmptyStateView(
            refreshPhase: viewModel.refreshPhase,
            onRefresh: viewModel.pullToRefresh
        )
    }

    private func openSource(_ sourceKey: String) {
        guard let source = viewModel.source(for: sourceKey) else { return }
        activeSource = BriefingSourceSheetItem(source: source)
    }

    private func openDiscussion(_ source: APIBriefingSource) {
        activeSource = BriefingSourceSheetItem(
            source: source,
            initialScrollTarget: .comments
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

    private func toggleNarration(lensKey: String) async {
        narrationError = nil
        let target = viewModel.narrationEpisode(for: lensKey).map { NarrationTarget.audioEpisode($0.id) }
        if let target,
           playbackService.speakingTarget == target,
           playbackService.isSpeaking {
            playbackService.pause()
            return
        }

        preparingNarrationLensKeys.insert(lensKey)
        defer { preparingNarrationLensKeys.remove(lensKey) }

        do {
            let episode = try await viewModel.prepareNarration(for: lensKey)
            try await playbackService.playStreamingNarration(
                for: .audioEpisode(episode.id),
                rate: playbackService.playbackRate
            ) {
                try await viewModel.narrationStreamResource(for: episode)
            }
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            briefingNarrationLogger.error(
                "Narration playback failed | lensKey=\(lensKey, privacy: .public) error=\(error.localizedDescription, privacy: .private)"
            )
            narrationError = (error as? AudioEpisodeServiceError)?.userFacingMessage
                ?? AudioEpisodeServiceError.generationFailed.userFacingMessage
        }
    }
}
