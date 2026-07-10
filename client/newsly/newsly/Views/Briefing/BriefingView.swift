import os.log
import SwiftUI

private let briefingNarrationLogger = Logger(
    subsystem: "com.newsly",
    category: "BriefingNarration"
)

struct BriefingView: View {
    @ObservedObject var viewModel: BriefingViewModel

    @StateObject private var digViewModel = BriefingDigViewModel(service: LiveBriefingService())
    @State private var playbackService = NarrationPlaybackService.shared
    @State private var activeSource: BriefingSourceSheetItem?
    @State private var preparingNarrationLensKeys: Set<String> = []
    @State private var narrationError: String?
    @State private var mastheadHeight: CGFloat = 170
    @State private var categoryStripHeight: CGFloat = 90

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

    private var collapsibleChromeHeight: CGFloat {
        mastheadHeight + (viewModel.isCategoryStripExpanded ? categoryStripHeight : 0)
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
        .sheet(item: $activeSource) { item in
            BriefingSourceSheet(
                item: item,
                contentIds: contentIdsForCurrentLens(matching: item.source.kind)
            )
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
        .task {
            await viewModel.loadIndexIfNeeded()
        }
    }

    private var briefingContent: some View {
        VStack(spacing: 0) {
            headerChrome

            TabView(selection: selectedLensBinding) {
                ForEach(viewModel.pagerLenses, id: \.key) { lens in
                    BriefingLensPageView(
                        lensSummary: lens,
                        lens: viewModel.lenses[lens.key],
                        viewModel: viewModel,
                        collapsibleChromeHeight: collapsibleChromeHeight,
                        onOpenSource: openSource,
                        onOpenDiscussion: openDiscussion,
                        onDig: startDig
                    )
                    .tag(lens.key)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            // Tier switches swap the whole page set; re-identifying the pager
            // rebuilds it cleanly instead of animating across stale pages.
            .id(viewModel.isNewsTierSelected ? "tier:news" : "lens:\(viewModel.selectedLensKey ?? "")")
            .accessibilityIdentifier("briefing.lens_pager")
        }
        .animation(.easeInOut(duration: 0.22), value: viewModel.isMastheadCompact)
        .animation(.easeInOut(duration: 0.22), value: viewModel.isCategoryStripExpanded)
    }

    /// Everything above the pager — masthead, tier strip, category strip, and
    /// the playback panel — stays pinned while pages swipe underneath. The
    /// masthead collapses on scroll; today's date (not the generation
    /// timestamp) keeps the kicker identical to the Knowledge tab's masthead.
    private var headerChrome: some View {
        VStack(spacing: 0) {
            if !viewModel.isMastheadCompact {
                EditorialMastheadHeader(
                    title: "Briefing",
                    trailingAccessory: mastheadListenAccessory,
                    accessoryAlignment: .title
                )
                .onGeometryChange(for: CGFloat.self) { proxy in
                    proxy.size.height
                } action: { _, height in
                    mastheadHeight = max(height, 0)
                }
                .transition(.move(edge: .top).combined(with: .opacity))
            }

            BriefingTierStrip(
                viewModel: viewModel,
                onSelectNews: { viewModel.selectNewsTier() },
                onSelectLens: { key in viewModel.selectLens(key: key) }
            )

            if viewModel.isCategoryStripExpanded {
                BriefingCategoryStrip(viewModel: viewModel) { key in
                    withAnimation(.easeInOut(duration: 0.22)) {
                        viewModel.selectLens(key: key)
                    }
                }
                .transition(.move(edge: .top).combined(with: .opacity))
                .onGeometryChange(for: CGFloat.self) { proxy in
                    proxy.size.height
                } action: { _, height in
                    categoryStripHeight = max(height, 0)
                }
            }

            if let lensKey = viewModel.selectedLensKey {
                listenPanel(lensKey: lensKey)
            }
        }
        .background(Color.surfacePrimary)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(viewModel.isMastheadCompact ? 0.5 : 0))
                .frame(height: 0.5)
        }
        .shadow(color: .black.opacity(viewModel.isMastheadCompact ? 0.12 : 0), radius: 10, y: 5)
        .zIndex(1)
    }

    private var mastheadListenAccessory: AnyView? {
        viewModel.selectedLensKey.map { lensKey in
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
        EmptyStateView(
            icon: "newspaper",
            title: "No briefing yet",
            subtitle: "Pull to refresh after new unread sources arrive.",
            actionTitle: "Refresh",
            action: {
                Task { await viewModel.pullToRefresh() }
            }
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
