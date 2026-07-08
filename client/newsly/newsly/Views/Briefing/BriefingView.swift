import MarkdownUI
import SwiftUI

struct BriefingView: View {
    @ObservedObject var viewModel: BriefingViewModel

    @StateObject private var digViewModel = BriefingDigViewModel(service: LiveBriefingService())
    @State private var playbackService = NarrationPlaybackService.shared
    @State private var activeSource: BriefingSourceSheetItem?
    @State private var activeDiscussion: BriefingDiscussionSheetItem?
    @State private var preparingNarrationLensKeys: Set<String> = []
    @State private var narrationError: String?
    // Last measured heights of the chrome that hides on scroll; pages use the
    // sum to decide whether collapsing would leave them enough scroll range.
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
        .sheet(item: $activeDiscussion) { item in
            BriefingDiscussionSheet(item: item)
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
                    mastheadHeight = height
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
                .onGeometryChange(for: CGFloat.self) { proxy in
                    proxy.size.height
                } action: { _, height in
                    categoryStripHeight = height
                }
                .transition(.move(edge: .top).combined(with: .opacity))
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

    /// Vertical space the chrome hands back when the reader scrolls into a
    /// page — what the masthead (and category strip, in news) occupy now.
    private var collapsibleChromeHeight: CGFloat {
        mastheadHeight + (viewModel.isCategoryStripExpanded ? categoryStripHeight : 0)
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
        activeDiscussion = BriefingDiscussionSheetItem(source: source)
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
            let episode = try await readyNarrationEpisode(lensKey: lensKey)
            try await playbackService.playStreamingNarration(
                for: .audioEpisode(episode.id),
                rate: playbackService.playbackRate
            ) {
                try await AudioEpisodeService.shared.streamResource(for: episode)
            }
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            narrationError = error.localizedDescription
        }
    }

    private func readyNarrationEpisode(lensKey: String) async throws -> AudioEpisode {
        var episode = viewModel.narrationEpisode(for: lensKey)
        if episode == nil {
            episode = await viewModel.requestNarration(for: lensKey)
        }
        guard var current = episode else {
            throw BriefingNarrationError.couldNotStart
        }
        if current.isGenerating {
            current = try await AudioEpisodeService.shared.waitForCompletedEpisode(current)
            viewModel.storeNarrationEpisode(current, for: lensKey)
        }
        guard current.isCompleted else {
            throw BriefingNarrationError.notReady(current.errorMessage)
        }
        return current
    }
}

private enum BriefingNarrationError: LocalizedError {
    case couldNotStart
    case notReady(String?)

    var errorDescription: String? {
        switch self {
        case .couldNotStart:
            return "Could not start narration."
        case .notReady(let message):
            return message ?? "Narration is not ready yet."
        }
    }
}

/// Top-level pills: one aggregate "News" pill plus every fixed (podcasts /
/// articles) lens. Lives above the pager so it stays put while pages swipe.
private struct BriefingTierStrip: View {
    @ObservedObject var viewModel: BriefingViewModel
    let onSelectNews: () -> Void
    let onSelectLens: (String) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if !viewModel.newsLenses.isEmpty {
                    BriefingStripPill(
                        title: "News",
                        unreadCount: viewModel.newsUnreadSourceCount,
                        isSelected: viewModel.isNewsTierSelected,
                        accessibilityId: "briefing.tier.news",
                        action: onSelectNews
                    )
                }

                ForEach(viewModel.fixedLenses, id: \.key) { lens in
                    BriefingStripPill(
                        title: lens.title,
                        unreadCount: lens.unreadSourceCount,
                        isSelected: lens.key == viewModel.selectedLensKey,
                        accessibilityId: "briefing.lens.\(lens.key)"
                    ) {
                        onSelectLens(lens.key)
                    }
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 10)
        }
        .accessibilityIdentifier("briefing.lenses")
    }
}

/// News categories revealed by the News pill, stacked into two packed rows
/// that scroll together; the pager swipes through exactly these, so the
/// selected pill follows the swipe.
private struct BriefingCategoryStrip: View {
    @ObservedObject var viewModel: BriefingViewModel
    let onSelectLens: (String) -> Void

    /// Even indices on top, odd below, so neighbors in swipe order sit next
    /// to each other. A handful of categories stays on a single row.
    private var rows: [[APIBriefingLensSummary]] {
        let lenses = viewModel.newsLenses
        guard lenses.count >= 4 else { return [lenses] }
        var top: [APIBriefingLensSummary] = []
        var bottom: [APIBriefingLensSummary] = []
        for (index, lens) in lenses.enumerated() {
            if index.isMultiple(of: 2) {
                top.append(lens)
            } else {
                bottom.append(lens)
            }
        }
        return [top, bottom]
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                        HStack(spacing: 8) {
                            ForEach(row, id: \.key) { lens in
                                BriefingStripPill(
                                    title: lens.title,
                                    unreadCount: lens.unreadSourceCount,
                                    isSelected: lens.key == viewModel.selectedLensKey,
                                    accessibilityId: "briefing.lens.\(lens.key)",
                                    minHeight: 30
                                ) {
                                    onSelectLens(lens.key)
                                }
                                .id(lens.key)
                            }
                        }
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 10)
            }
            .onAppear {
                guard let selectedKey = viewModel.selectedLensKey else { return }
                proxy.scrollTo(selectedKey, anchor: .center)
            }
            .onChange(of: viewModel.selectedLensKey) { _, selectedKey in
                guard let selectedKey else { return }
                withAnimation(.easeInOut(duration: 0.22)) {
                    proxy.scrollTo(selectedKey, anchor: .center)
                }
            }
        }
        .accessibilityIdentifier("briefing.categories")
    }
}

private struct BriefingStripPill: View {
    let title: String
    let unreadCount: Int
    let isSelected: Bool
    let accessibilityId: String
    var minHeight: CGFloat = 36
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(title)
                    .font(.appCaption.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)

                if unreadCount > 0 {
                    Text("\(unreadCount)")
                        .font(.appCaption2.weight(.bold).monospacedDigit())
                        .foregroundStyle(isSelected ? Color.surfacePrimary : Color.brandPrimary)
                        .contentTransition(.numericText(countsDown: true))
                        .animation(.easeInOut(duration: 0.3), value: unreadCount)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(
                            Capsule()
                                .fill(isSelected ? Color.onSurface : Color.brandPrimary.opacity(0.12))
                        )
                }
            }
            .foregroundStyle(isSelected ? Color.surfacePrimary : Color.onSurface)
            .frame(minHeight: minHeight)
            .padding(.horizontal, 12)
            .background(
                Capsule()
                    .fill(isSelected ? Color.onSurface : Color.surfaceSecondary)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title), \(unreadCount) unread sources")
        .accessibilityIdentifier(accessibilityId)
    }
}

private struct BriefingLensPageView: View {
    let lensSummary: APIBriefingLensSummary
    let lens: APIBriefingLensResponse?
    @ObservedObject var viewModel: BriefingViewModel
    let collapsibleChromeHeight: CGFloat
    let onOpenSource: (String) -> Void
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void

    @State private var isPinned = false

    // Scroll position quantized to 64pt steps: crossing the first step
    // collapses the masthead, and any step increase collapses a tap-opened
    // category strip — without streaming raw offsets into the view model.
    private static let scrollStep: CGFloat = 64

    /// The scroll facts the chrome cares about, coarse enough that the
    /// closure below fires on step boundaries instead of every frame.
    private struct ScrollProbe: Equatable {
        var step = 0
        var hasCollapseHeadroom = false
    }

    var body: some View {
        Group {
            if let lens {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 24) {
                        let sourcesByKey = Dictionary(
                            lens.sources.map { ($0.sourceKey, $0) },
                            uniquingKeysWith: { current, _ in current }
                        )
                        ForEach(lens.segments, id: \.id) { segment in
                            BriefingSegmentView(
                                segment: segment,
                                sourcesByKey: sourcesByKey,
                                onOpenSource: onOpenSource,
                                onOpenDiscussion: onOpenDiscussion,
                                onDig: onDig,
                                onSourceKeysSeen: { sourceKeys in
                                    viewModel.markSourcesSeen(sourceKeys)
                                }
                            )
                            .id(segment.id)
                            // Seen = the segment's bottom scrolled past the top edge.
                            .onGeometryChange(for: Bool.self) { proxy in
                                proxy.frame(in: .scrollView).maxY < 0
                            } action: { _, exitedTop in
                                guard exitedTop else { return }
                                viewModel.markSegmentSeen(segment)
                            }
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                        }

                        Color.clear
                            .frame(height: 24)
                            .accessibilityHidden(true)
                    }
                    .padding(.top, 4)
                }
                .refreshable {
                    await viewModel.pullToRefresh()
                }
                .onScrollGeometryChange(for: ScrollProbe.self) { geometry in
                    let offset = geometry.contentOffset.y + geometry.contentInsets.top
                    let scrollableRange = geometry.contentSize.height - geometry.containerSize.height
                    return ScrollProbe(
                        step: Int((offset / Self.scrollStep).rounded(.down)),
                        // Collapsing hands the chrome's height to this scroll
                        // view; without at least that much range to spare, the
                        // offset would clamp back under the threshold and
                        // bounce the chrome right back in.
                        hasCollapseHeadroom: scrollableRange
                            >= collapsibleChromeHeight + 2 * Self.scrollStep
                    )
                } action: { oldProbe, probe in
                    if probe.step > oldProbe.step, probe.step >= 1 {
                        viewModel.noteScrolledDown(forLens: lensSummary.key)
                    }
                    // Headroom gates only the collapse; once collapsed, the
                    // shrunken range must not read as a reason to expand.
                    let pinned = probe.step >= 1 && (isPinned || probe.hasCollapseHeadroom)
                    isPinned = pinned
                    viewModel.setHeaderPinned(pinned, forLens: lensSummary.key)
                }
                .accessibilityIdentifier("briefing.lens_page.\(lensSummary.key)")
            } else {
                LoadingView()
                    .onAppear {
                        viewModel.loadLensIfNeeded(key: lensSummary.key)
                    }
            }
        }
    }
}

private struct BriefingSegmentView: View {
    let segment: APIBriefingSegment
    let sourcesByKey: [String: APIBriefingSource]
    let onOpenSource: (String) -> Void
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void
    let onSourceKeysSeen: ([String]) -> Void
    private let displayBlocks: [DisplayBlock]
    private let discussionChipsByBlockIndex: [Int: [String: BriefingDiscussionChip]]
    private let allSourcesRead: Bool

    private enum DisplayBlock: Identifiable {
        case single(Int, APIBriefingBlock)
        case floatingFigure(Int, figure: APIBriefingBlock, passage: APIBriefingBlock, passageIndex: Int)

        var id: Int {
            switch self {
            case .single(let index, _), .floatingFigure(let index, _, _, _):
                return index
            }
        }
    }

    init(
        segment: APIBriefingSegment,
        sourcesByKey: [String: APIBriefingSource],
        onOpenSource: @escaping (String) -> Void,
        onOpenDiscussion: @escaping (APIBriefingSource) -> Void,
        onDig: @escaping (String, String) -> Void,
        onSourceKeysSeen: @escaping ([String]) -> Void
    ) {
        self.segment = segment
        self.sourcesByKey = sourcesByKey
        self.onOpenSource = onOpenSource
        self.onOpenDiscussion = onOpenDiscussion
        self.onDig = onDig
        self.onSourceKeysSeen = onSourceKeysSeen
        self.displayBlocks = Self.displayBlocks(for: segment.blocks, sourcesByKey: sourcesByKey)
        let chipsByBlockIndex = Self.discussionChipsByBlockIndex(
            for: segment.blocks,
            sourcesByKey: sourcesByKey
        )
        self.discussionChipsByBlockIndex = chipsByBlockIndex
        self.allSourcesRead = Self.allSourcesRead(segment.sourceKeys, sourcesByKey: sourcesByKey)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(displayBlocks) { item in
                switch item {
                case .single(let index, let block):
                    blockView(block, blockIndex: index)
                case .floatingFigure(_, let figure, let passage, let passageIndex):
                    BriefingFloatingFigurePassage(
                        figure: figure,
                        passage: passage,
                        source: source(for: figure),
                        discussionChips: discussionChipsByBlockIndex[passageIndex] ?? [:],
                        figureOpacity: readOpacity(for: figure.briefingDirectSourceKeys),
                        passageOpacity: readOpacity(for: passage.briefingFallbackReadSourceKeys),
                        onOpenSource: onOpenSource,
                        onOpenDiscussion: openDiscussion(forSourceKey:),
                        onDig: onDig,
                        onSourceKeysSeen: onSourceKeysSeen
                    )
                }
            }
        }
        .padding(.vertical, 2)
        // Read segments recede without losing legibility.
        .opacity(allSourcesRead ? 0.72 : 1)
        .animation(.easeInOut(duration: 0.35), value: allSourcesRead)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("briefing.segment.\(segment.id)")
    }

    private static func allSourcesRead(
        _ sourceKeys: [String],
        sourcesByKey: [String: APIBriefingSource]
    ) -> Bool {
        !sourceKeys.isEmpty
            && sourceKeys.allSatisfy { sourcesByKey[$0]?.read ?? true }
    }

    /// Inset figures adjacent to a meaty passage float inside it (text wraps);
    /// everything else renders block-by-block as before.
    private static func displayBlocks(
        for blocks: [APIBriefingBlock],
        sourcesByKey: [String: APIBriefingSource]
    ) -> [DisplayBlock] {
        var items: [DisplayBlock] = []
        var index = 0
        while index < blocks.count {
            let block = blocks[index]
            if isFloatableFigure(block, sourcesByKey: sourcesByKey),
               index + 1 < blocks.count,
               isFloatHost(blocks[index + 1]) {
                items.append(.floatingFigure(
                    index,
                    figure: block,
                    passage: blocks[index + 1],
                    passageIndex: index + 1
                ))
                index += 2
                continue
            }
            if isFloatHost(block),
               index + 1 < blocks.count,
               isFloatableFigure(blocks[index + 1], sourcesByKey: sourcesByKey) {
                items.append(.floatingFigure(
                    index,
                    figure: blocks[index + 1],
                    passage: block,
                    passageIndex: index
                ))
                index += 2
                continue
            }
            items.append(.single(index, block))
            index += 1
        }
        return items
    }

    /// Assigns each discussion-bearing source an inline chip on the first
    /// passage block that links to it, so the affordance appears exactly once
    /// per segment, in the sentence that cites the story.
    private static func discussionChipsByBlockIndex(
        for blocks: [APIBriefingBlock],
        sourcesByKey: [String: APIBriefingSource]
    ) -> [Int: [String: BriefingDiscussionChip]] {
        var assignedSourceKeys = Set<String>()
        var chipsByBlockIndex: [Int: [String: BriefingDiscussionChip]] = [:]
        for (index, block) in blocks.enumerated() where block.type == .passage {
            for sourceKey in block.briefingSourceLinkKeys {
                guard !assignedSourceKeys.contains(sourceKey),
                      let source = sourcesByKey[sourceKey],
                      source.kind == "news",
                      let discussion = source.discussion
                else { continue }
                assignedSourceKeys.insert(sourceKey)
                chipsByBlockIndex[index, default: [:]][sourceKey] = BriefingDiscussionChip(
                    sourceKey: sourceKey,
                    commentCount: discussion.commentCount
                )
            }
        }
        return chipsByBlockIndex
    }

    private func openDiscussion(forSourceKey sourceKey: String) {
        guard let source = sourcesByKey[sourceKey] else { return }
        onOpenDiscussion(source)
    }

    private static func isFloatableFigure(
        _ block: APIBriefingBlock,
        sourcesByKey: [String: APIBriefingSource]
    ) -> Bool {
        block.type == .figure && block.placement == "inset" && hasImage(block, sourcesByKey: sourcesByKey)
    }

    private static func isFloatHost(_ block: APIBriefingBlock) -> Bool {
        block.type == .passage && plainTextLength(of: block) >= 240
    }

    private static func plainTextLength(of block: APIBriefingBlock) -> Int {
        (block.paragraphs ?? []).reduce(0) { total, paragraph in
            total + paragraph.runs.reduce(0) { $0 + $1.text.count }
        }
    }

    private static func hasImage(
        _ block: APIBriefingBlock,
        sourcesByKey: [String: APIBriefingSource]
    ) -> Bool {
        let source = block.sourceKey.flatMap { sourcesByKey[$0] }
        let url = block.imageUrl ?? block.thumbnailUrl ?? source?.imageUrl ?? source?.thumbnailUrl
        return url?.isEmpty == false
    }

    @ViewBuilder
    private func blockView(_ block: APIBriefingBlock, blockIndex: Int) -> some View {
        switch block.type {
        case .passage:
            BriefingPassageReadMarker(
                block: block,
                discussionChips: discussionChipsByBlockIndex[blockIndex] ?? [:],
                onOpenSource: onOpenSource,
                onOpenDiscussion: openDiscussion(forSourceKey:),
                onDig: onDig,
                onSourceKeysSeen: onSourceKeysSeen
            )
            .opacity(readOpacity(for: block.briefingFallbackReadSourceKeys))
            .animation(.easeInOut(duration: 0.35), value: readOpacity(for: block.briefingFallbackReadSourceKeys))
        case .figure:
            BriefingFigureView(
                block: block,
                source: source(for: block),
                onOpenSource: onOpenSource
            )
            .opacity(readOpacity(for: block.briefingDirectSourceKeys))
            .animation(.easeInOut(duration: 0.35), value: readOpacity(for: block.briefingDirectSourceKeys))
            .briefingSourceReadMarker(
                sourceKeys: block.briefingDirectSourceKeys,
                onSourceKeysSeen: onSourceKeysSeen
            )
        case .pullquote:
            BriefingPullquoteView(
                block: block,
                source: source(for: block),
                onOpenSource: onOpenSource
            )
            .opacity(readOpacity(for: block.briefingDirectSourceKeys))
            .animation(.easeInOut(duration: 0.35), value: readOpacity(for: block.briefingDirectSourceKeys))
            .briefingSourceReadMarker(
                sourceKeys: block.briefingDirectSourceKeys,
                onSourceKeysSeen: onSourceKeysSeen
            )
        }
    }

    private func readOpacity(for sourceKeys: [String]) -> Double {
        guard !allSourcesRead, !sourceKeys.isEmpty else { return 1 }
        return sourceKeys.allSatisfy { sourcesByKey[$0]?.read ?? true } ? 0.72 : 1
    }

    private func source(for block: APIBriefingBlock) -> APIBriefingSource? {
        block.sourceKey.flatMap { sourcesByKey[$0] }
    }
}

private struct BriefingPassageReadMarker: View {
    let block: APIBriefingBlock
    var floatingExclusionSize: CGSize? = nil
    var discussionChips: [String: BriefingDiscussionChip] = [:]
    let onOpenSource: (String) -> Void
    var onOpenDiscussion: (String) -> Void = { _ in }
    let onDig: (String, String) -> Void
    let onSourceKeysSeen: ([String]) -> Void

    @State private var sourceLinkPositions: [BriefingSourceLinkPosition] = []
    @State private var markedSourceKeys = Set<String>()

    var body: some View {
        BriefingPassageView(
            block: block,
            floatingExclusionSize: floatingExclusionSize,
            discussionChips: discussionChips,
            onOpenSource: onOpenSource,
            onOpenDiscussion: onOpenDiscussion,
            onDig: onDig,
            onSourceLinkPositionsChange: { positions in
                sourceLinkPositions = positions
            }
        )
        .onGeometryChange(for: [String].self) { proxy in
            let frame = proxy.frame(in: .scrollView)
            let exitedLinkKeys = sourceLinkPositions.compactMap { position -> String? in
                frame.minY + position.maxY < 0 ? position.sourceKey : nil
            }
            let directKeys = frame.maxY < 0 ? block.briefingDirectSourceKeys : []
            let fallbackLinkKeys = sourceLinkPositions.isEmpty && frame.maxY < 0
                ? block.briefingSourceLinkKeys
                : []
            return uniqueBriefingSourceKeys(exitedLinkKeys + directKeys + fallbackLinkKeys)
        } action: { _, sourceKeys in
            let newSourceKeys = sourceKeys.filter { markedSourceKeys.insert($0).inserted }
            guard !newSourceKeys.isEmpty else { return }
            onSourceKeysSeen(newSourceKeys)
        }
    }
}

private struct BriefingSourceReadMarker: ViewModifier {
    let sourceKeys: [String]
    let onSourceKeysSeen: ([String]) -> Void

    @State private var didMark = false

    func body(content: Content) -> some View {
        content
            .onGeometryChange(for: Bool.self) { proxy in
                proxy.frame(in: .scrollView).maxY < 0
            } action: { _, exitedTop in
                guard exitedTop, !didMark, !sourceKeys.isEmpty else { return }
                didMark = true
                onSourceKeysSeen(sourceKeys)
            }
    }
}

private extension View {
    func briefingSourceReadMarker(
        sourceKeys: [String],
        onSourceKeysSeen: @escaping ([String]) -> Void
    ) -> some View {
        modifier(
            BriefingSourceReadMarker(
                sourceKeys: sourceKeys,
                onSourceKeysSeen: onSourceKeysSeen
            )
        )
    }
}

private struct BriefingFloatingFigurePassage: View {
    let figure: APIBriefingBlock
    let passage: APIBriefingBlock
    let source: APIBriefingSource?
    var discussionChips: [String: BriefingDiscussionChip] = [:]
    let figureOpacity: Double
    let passageOpacity: Double
    let onOpenSource: (String) -> Void
    var onOpenDiscussion: (String) -> Void = { _ in }
    let onDig: (String, String) -> Void
    let onSourceKeysSeen: ([String]) -> Void

    private static let imageSize = CGSize(width: 148, height: 148)
    // Exclusion adds the text gutter around the floated image.
    private static let exclusionSize = CGSize(width: 162, height: 160)

    var body: some View {
        ZStack(alignment: .topTrailing) {
            BriefingPassageReadMarker(
                block: passage,
                floatingExclusionSize: Self.exclusionSize,
                discussionChips: discussionChips,
                onOpenSource: onOpenSource,
                onOpenDiscussion: onOpenDiscussion,
                onDig: onDig,
                onSourceKeysSeen: onSourceKeysSeen
            )
            .frame(maxWidth: .infinity, alignment: .leading)
            .opacity(passageOpacity)
            .animation(.easeInOut(duration: 0.35), value: passageOpacity)

            Button {
                if let sourceKey = figure.sourceKey ?? source?.sourceKey {
                    onOpenSource(sourceKey)
                }
            } label: {
                CachedAsyncImage(
                    url: ServerImageURL.resolve(figure.imageUrl ?? source?.imageUrl),
                    thumbnailUrl: ServerImageURL.resolve(figure.thumbnailUrl ?? source?.thumbnailUrl),
                    targetSize: Self.imageSize
                ) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } placeholder: {
                    Rectangle()
                        .fill(Color.surfaceSecondary)
                }
                .frame(width: Self.imageSize.width, height: Self.imageSize.height)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
            .buttonStyle(.plain)
            .opacity(figureOpacity)
            .animation(.easeInOut(duration: 0.35), value: figureOpacity)
            .briefingSourceReadMarker(
                sourceKeys: figure.briefingDirectSourceKeys,
                onSourceKeysSeen: onSourceKeysSeen
            )
            .accessibilityLabel(source?.title ?? "Article image")
        }
    }
}

private struct BriefingFigureView: View {
    let block: APIBriefingBlock
    let source: APIBriefingSource?
    let onOpenSource: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            CachedAsyncImage(
                url: ServerImageURL.resolve(block.imageUrl ?? source?.imageUrl),
                thumbnailUrl: ServerImageURL.resolve(block.thumbnailUrl ?? source?.thumbnailUrl),
                targetSize: CGSize(width: 640, height: 360)
            ) { image in
                image
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(maxWidth: .infinity)
                    .frame(height: block.placement == "inset" ? 176 : 216)
                    .clipped()
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            } placeholder: {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.surfaceSecondary)
                    .frame(height: block.placement == "inset" ? 176 : 216)
                    .overlay {
                        Image(systemName: "photo")
                            .font(.appSymbol(size: 24, weight: .light))
                            .foregroundStyle(Color.onSurfaceTertiary)
                    }
            }

            if let caption = block.caption ?? source?.title {
                Button {
                    if let sourceKey = block.sourceKey ?? source?.sourceKey {
                        onOpenSource(sourceKey)
                    }
                } label: {
                    Text(caption)
                        .font(.appCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .multilineTextAlignment(.leading)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.plain)
            }
        }
    }
}

private struct BriefingPullquoteView: View {
    let block: APIBriefingBlock
    let source: APIBriefingSource?
    let onOpenSource: (String) -> Void

    var body: some View {
        Button {
            if let sourceKey = block.sourceKey ?? source?.sourceKey {
                onOpenSource(sourceKey)
            }
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                Text(block.text ?? "")
                    .font(.appSerifItalic(size: 20, relativeTo: .title3))
                    .foregroundStyle(Color.onSurface)
                    .fixedSize(horizontal: false, vertical: true)

                if let title = source?.title {
                    Text(title)
                        .font(.appCaption.weight(.semibold))
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                }
            }
            .padding(.leading, 14)
            .overlay(alignment: .leading) {
                Rectangle()
                    .fill(Color.brandPrimary)
                    .frame(width: 3)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Quote from \(source?.title ?? "source")")
    }
}

private func briefingPlatformName(_ platform: String) -> String {
    switch platform.lowercased() {
    case "hackernews":
        return "Hacker News"
    case "reddit":
        return "Reddit"
    default:
        return platform
    }
}

/// Compact capsule that lives in the lens header; playback controls expand
/// below the header only while this lens is preparing or playing.
private struct BriefingListenButton: View {
    let isPreparing: Bool
    let isPlaying: Bool
    let onToggle: () -> Void

    var body: some View {
        Button(action: onToggle) {
            HStack(spacing: 5) {
                if isPreparing {
                    ProgressView()
                        .controlSize(.mini)
                        .tint(Color.brandPrimary)
                } else {
                    Image(systemName: isPlaying ? "pause.fill" : "headphones")
                        .font(.appSymbol(size: 11, weight: .semibold))
                }

                Text(isPlaying ? "Pause" : "Listen")
                    .font(.appCaption.weight(.semibold))
            }
            .foregroundStyle(Color.brandPrimary)
            .padding(.horizontal, 12)
            .frame(height: 30)
            .background(Capsule().fill(Color.brandPrimary.opacity(0.12)))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(isPreparing)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityIdentifier("briefing.narration.play")
    }

    private var accessibilityLabel: String {
        if isPreparing {
            return "Preparing briefing audio"
        }
        return isPlaying ? "Pause briefing audio" : "Play briefing audio"
    }
}

private struct BriefingDigSheet: View {
    @ObservedObject var viewModel: BriefingDigViewModel

    @State private var safariItem: BriefingSafariItem?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    fragmentHeader
                    stateContent
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .background(Color.surfacePrimary)
            .navigationTitle("Dig Deeper")
            .navigationBarTitleDisplayMode(.inline)
            .animation(.easeInOut(duration: 0.25), value: viewModel.stateKey)
            .accessibilityIdentifier("briefing.dig_sheet")
        }
        .sheet(item: $safariItem) { item in
            SafariView(url: item.url)
                .ignoresSafeArea()
        }
    }

    private var fragmentHeader: some View {
        Label(viewModel.fragment ?? "Dig Deeper", systemImage: "magnifyingglass")
            .font(.appCallout.weight(.semibold))
            .foregroundStyle(Color.onSurfaceSecondary)
            .lineLimit(2)
    }

    @ViewBuilder
    private var stateContent: some View {
        switch viewModel.state {
        case .idle:
            EmptyView()
        case .searching:
            loadingRow("Searching the web…")
                .transition(.opacity)
        case .summarizing(let results):
            VStack(alignment: .leading, spacing: 16) {
                loadingRow("Summarizing…")
                resultLinks(results)
            }
            .transition(.opacity)
        case .loaded(let results, let summary):
            VStack(alignment: .leading, spacing: 16) {
                Markdown(BriefingDigViewModel.citationLinkedMarkdown(summary))
                    .markdownTheme(.chat)
                    .environment(\.openURL, citationOpenAction(results: results))
                resultLinks(results)
            }
            .transition(.opacity)
        case .error(let message):
            Text(message)
                .font(.appCallout)
                .foregroundStyle(Color.statusDestructive)
                .transition(.opacity)
        }
    }

    private func loadingRow(_ label: String) -> some View {
        HStack(spacing: 10) {
            ProgressView()
            Text(label)
                .font(.appCallout)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
    }

    private func citationOpenAction(results: [APIBriefingDigSearchResult]) -> OpenURLAction {
        OpenURLAction { url in
            guard url.scheme == "digsource",
                  let index = Int(url.host ?? ""),
                  index >= 1,
                  index <= results.count,
                  let sourceURL = URL(string: results[index - 1].url)
            else { return .systemAction }
            safariItem = BriefingSafariItem(url: sourceURL)
            return .handled
        }
    }

    @ViewBuilder
    private func resultLinks(_ results: [APIBriefingDigSearchResult]) -> some View {
        if !results.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("Sources")
                    .kicker()
                ForEach(Array(results.prefix(5).enumerated()), id: \.offset) { _, result in
                    if let url = URL(string: result.url) {
                        Button {
                            safariItem = BriefingSafariItem(url: url)
                        } label: {
                            Text(result.title)
                                .font(.appCallout.weight(.semibold))
                                .foregroundStyle(Color.brandPrimary)
                                .lineLimit(2)
                                .multilineTextAlignment(.leading)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }
}

struct BriefingSourceSheetItem: Identifiable {
    let source: APIBriefingSource

    var id: String {
        source.sourceKey
    }
}

struct BriefingSafariItem: Identifiable {
    let url: URL

    var id: String {
        url.absoluteString
    }
}

struct BriefingDiscussionSheetItem: Identifiable {
    let source: APIBriefingSource

    var id: String {
        source.sourceKey
    }
}

private enum BriefingDiscussionLoadState {
    case loading
    case loaded(ContentDiscussion)
    case error(String)
}

private struct BriefingDiscussionSheet: View {
    let item: BriefingDiscussionSheetItem

    @Environment(\.dismiss) private var dismiss
    @State private var state: BriefingDiscussionLoadState = .loading
    @State private var safariItem: BriefingSafariItem?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    stateContent
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .background(Color.surfacePrimary)
            .navigationTitle("Discussion")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
        .task(id: item.id) {
            await loadDiscussion()
        }
        .sheet(item: $safariItem) { item in
            SafariView(url: item.url)
                .ignoresSafeArea()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Community Reaction")
                .kicker()
            Text(item.source.title)
                .font(.appTitle3)
                .foregroundStyle(Color.onSurface)
                .fixedSize(horizontal: false, vertical: true)
            if let discussion = item.source.discussion {
                Text(countLabel(for: discussion))
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    @ViewBuilder
    private var stateContent: some View {
        switch state {
        case .loading:
            HStack(spacing: 10) {
                ProgressView()
                Text("Loading discussion...")
                    .font(.appCallout)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
        case .loaded(let discussion):
            if discussion.summary != nil {
                DiscussionSummaryView(discussion: discussion, onOpenURL: openURL)
            } else {
                unavailableContent(discussion.unavailableMessage)
            }
        case .error(let message):
            unavailableContent(message)
        }
    }

    private func unavailableContent(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(message)
                .font(.appCallout)
                .foregroundStyle(Color.onSurfaceSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if let rawURL = item.source.discussion?.externalUrl,
               let url = URL(string: rawURL) {
                Button {
                    safariItem = BriefingSafariItem(url: url)
                } label: {
                    Label("Open thread", systemImage: "arrow.up.right.square")
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    private func loadDiscussion() async {
        state = .loading
        do {
            let discussion = try await ContentService.shared.fetchContentDiscussion(
                id: item.source.id,
                contentType: .news
            )
            state = .loaded(discussion)
        } catch where isNetworkCancellation(error) {
            return
        } catch {
            state = .error("Discussion could not be loaded right now.")
        }
    }

    private func openURL(_ url: URL) {
        safariItem = BriefingSafariItem(url: url)
    }

    private func countLabel(for discussion: APIBriefingDiscussion) -> String {
        if let count = discussion.commentCount {
            return "\(count) \(count == 1 ? "comment" : "comments") on \(briefingPlatformName(discussion.platform))"
        }
        return "Discussion on \(briefingPlatformName(discussion.platform))"
    }
}

/// Every briefing source opens the same reading screen the feeds use —
/// news sources get the full short-news article view, not an abridged card.
private struct BriefingSourceSheet: View {
    let item: BriefingSourceSheetItem
    let contentIds: [Int]

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ContentDetailView(
                contentId: item.source.id,
                contentType: item.source.contentType,
                allContentIds: contentIds,
                navigationSurface: .briefing
            )
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
}
