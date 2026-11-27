import SwiftUI

final class BriefingSegmentRenderModel: Identifiable {
    let segment: APIBriefingSegment
    let sourcesByKey: [String: APIBriefingSource]
    /// Present for one-source article and podcast segments, which render as cards.
    let card: BriefingSourceCard?
    let passageBlockIndices: [Int]
    let discussionChipsByBlockIndex: [Int: [String: BriefingDiscussionChip]]
    let passageContentByBlockIndex: [Int: BriefingAttributedTextBuilder.Result]
    let allSourcesRead: Bool

    var id: Int { segment.id }

    init(segment: APIBriefingSegment, sourcesByKey: [String: APIBriefingSource]) {
        let segmentSourcesByKey = Dictionary(
            uniqueKeysWithValues: segment.sourceKeys.compactMap { sourceKey in
                sourcesByKey[sourceKey].map { (sourceKey, $0) }
            }
        )
        self.segment = segment
        self.sourcesByKey = segmentSourcesByKey
        let card = BriefingSourceCard(segment: segment, sourcesByKey: segmentSourcesByKey)
        self.card = card
        self.passageBlockIndices = segment.blocks.indices.filter { segment.blocks[$0].type == .passage }
        let discussionChipsByBlockIndex = Self.discussionChipsByBlockIndex(
            for: segment.blocks,
            sourcesByKey: segmentSourcesByKey
        )
        self.discussionChipsByBlockIndex = discussionChipsByBlockIndex
        let aggregatorMonograms = segmentSourcesByKey.compactMapValues { source in
            source.aggregator.flatMap(BriefingAggregatorBadge.monogram(for:))
        }
        let textBuilder = BriefingAttributedTextBuilder()
        self.passageContentByBlockIndex = Dictionary(
            uniqueKeysWithValues: passageBlockIndices.map { index in
                (
                    index,
                    textBuilder.build(
                        paragraphs: segment.blocks[index].paragraphs ?? [],
                        discussionChips: discussionChipsByBlockIndex[index] ?? [:],
                        aggregatorMonograms: aggregatorMonograms,
                        bodyStyle: card == nil ? .passage : BriefingSourceCard.summaryStyle
                    )
                )
            }
        )
        self.allSourcesRead = !segment.sourceKeys.isEmpty
            && segment.sourceKeys.allSatisfy { segmentSourcesByKey[$0]?.read ?? true }
    }

    /// Assigns each discussion-bearing source an inline chip on the first
    /// passage block that links to it, so the affordance appears exactly once.
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
}

final class BriefingLensRenderModel {
    let hasMore: Bool
    let segments: [BriefingSegmentRenderModel]
    let timelineSeparatorSegmentIDs: Set<Int>
    /// Articles and Podcasts present each work as a card; News stays prose.
    let usesCards: Bool

    init(
        lens: APIBriefingLensResponse,
        reusing previous: BriefingLensRenderModel? = nil,
        affectedSourceKeys: Set<String>? = nil
    ) {
        let sourcesByKey = Dictionary(
            lens.sources.map { ($0.sourceKey, $0) },
            uniquingKeysWith: { current, _ in current }
        )
        self.hasMore = lens.hasMore
        self.usesCards = lens.lens.tier != .news
        let previousSegmentsByID = Dictionary(
            uniqueKeysWithValues: (previous?.segments ?? []).map { ($0.id, $0) }
        )
        self.segments = lens.segments.map { segment in
            if let affectedSourceKeys,
               affectedSourceKeys.isDisjoint(with: segment.sourceKeys),
               let reusable = previousSegmentsByID[segment.id] {
                return reusable
            }
            return BriefingSegmentRenderModel(segment: segment, sourcesByKey: sourcesByKey)
        }
        let separatorIndices = lens.lens.tier == .news
            ? BriefingTimelineSeparatorPolicy.separatorIndices(for: lens.segments.map(\.createdAt))
            : []
        self.timelineSeparatorSegmentIDs = Set(
            separatorIndices.map { lens.segments[$0].id }
        )
    }
}

struct BriefingLensPageView: View, Equatable {
    @Environment(\.displayScale) private var displayScale
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let lensKey: String
    let lensTitle: String
    let renderModel: BriefingLensRenderModel?
    var firstRunProgress: APIBriefingFirstRunTierProgress? = nil
    let isReadTrackingEnabled: Bool
    let readBoundaryY: CGFloat?
    let documentGeneration: Int
    let scrollToTopRequest: Int
    let shouldScrollToTop: Bool
    let error: String?
    let continuationError: String?
    let isLoadingContinuation: Bool
    var chromeCollapse: BriefingChromeCollapseModel
    let collapsibleChromeHeight: CGFloat
    /// Height of the fully expanded chrome; the scroll content is inset by
    /// this so its first line starts at the chrome's bottom edge and rides it
    /// 1:1 while the chrome collapses.
    let topContentInset: CGFloat
    /// Status-bar and home-indicator insets. The pager extends under both so
    /// text runs to the screen edges instead of being clipped by the page
    /// frame; pages add them back as content insets themselves.
    var screenEdgeInsets = EdgeInsets()
    let onOpenSource: (String) -> Void
    var onSourceAction: ((String, BriefingSourceAction) -> Void)? = nil
    var savedSources: BriefingSavedSources? = nil
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void
    let onRefresh: () async -> Void
    let onLoad: () -> Void
    let onRetry: () -> Void
    let onFirstPassageVisible: () -> Void
    let onScrolledDown: () -> Void
    let onMarkSegmentSeen: (APIBriefingSegment) -> Void
    let onSetHeaderPinned: (Bool) -> Void
    /// The open display's arrangement of this page. Nil is the phone pager.
    var columnLayout: BriefingColumnLayout? = nil
    /// Keeps a segment in view when News reflows (a story opens or closes).
    var anchorRequest: BriefingScrollAnchorRequest? = nil

    private var readingWidth: CGFloat? { columnLayout?.readingWidth }
    private var presentsCardsAsRows: Bool { columnLayout?.presentsCardsAsRows ?? false }
    private var selectedSourceKey: String? { columnLayout?.selectedSourceKey }

    @Environment(RootTabBarMinimizer.self) private var tabBarMinimizer: RootTabBarMinimizer?
    @State private var isPinned = false
    @State private var containerHeight: CGFloat = 0
    @State private var hasReportedFirstPassage = false
    @State private var readTracker = BriefingScrollReadTracker()

    private static let topAnchor = "briefing.lens.top"

    // Steps only gate the discrete signals (retiring a tap-opened category
    // strip); the chrome collapse itself follows the clamped offset below.
    private static let scrollStep: CGFloat = 64
    /// Hysteresis so the pinned flag doesn't flicker while the finger rests
    /// exactly on the collapse boundary.
    private static let unpinSlack: CGFloat = 12

    static func == (lhs: BriefingLensPageView, rhs: BriefingLensPageView) -> Bool {
        lhs.lensKey == rhs.lensKey
            && lhs.lensTitle == rhs.lensTitle
            && lhs.renderModel === rhs.renderModel
            && lhs.firstRunProgress == rhs.firstRunProgress
            && lhs.isReadTrackingEnabled == rhs.isReadTrackingEnabled
            && lhs.readBoundaryY == rhs.readBoundaryY
            && lhs.documentGeneration == rhs.documentGeneration
            && lhs.scrollToTopRequest == rhs.scrollToTopRequest
            && lhs.shouldScrollToTop == rhs.shouldScrollToTop
            && lhs.error == rhs.error
            && lhs.continuationError == rhs.continuationError
            && lhs.isLoadingContinuation == rhs.isLoadingContinuation
            && lhs.chromeCollapse === rhs.chromeCollapse
            && lhs.collapsibleChromeHeight == rhs.collapsibleChromeHeight
            && lhs.topContentInset == rhs.topContentInset
            && lhs.screenEdgeInsets == rhs.screenEdgeInsets
            && lhs.savedSources === rhs.savedSources
            && lhs.columnLayout == rhs.columnLayout
            && lhs.anchorRequest == rhs.anchorRequest
    }

    /// One scroll observer owns both chrome movement and read passage. The
    /// exact offset feeds a non-observable tracker, so steady scrolling does
    /// not invalidate the page unless a segment actually becomes read.
    private struct ScrollProbe: Equatable {
        var collapse: CGFloat = 0
        var step = 0
        var containerHeight: CGFloat = 0
        var readOffset: CGFloat = 0
        /// Distance from the top of content, clamped to the scrollable range.
        var scrolledDistance: CGFloat = 0
    }

    private var contentIdentity: BriefingLensContentIdentity {
        BriefingLensContentIdentity(
            lensKey: lensKey,
            generation: documentGeneration
        )
    }

    private var readTrackingConfiguration: BriefingReadTrackingConfiguration {
        BriefingReadTrackingConfiguration(
            documentGeneration: documentGeneration,
            segmentIDs: renderModel?.segments.map(\.id) ?? [],
            isEnabled: isReadTrackingEnabled,
            readBoundaryY: readBoundaryY
        )
    }

    private var pageInsets: BriefingPageInsets {
        briefingPageInsets(
            topContentInset: topContentInset,
            screenEdgeTop: screenEdgeInsets.top
        )
    }

    /// Guarantees every page can scroll far enough to fully collapse the
    /// chrome, so short pages never rest half-collapsed.
    private var minContentHeight: CGFloat? {
        guard containerHeight > 0 else { return nil }
        return max(containerHeight - topContentInset + collapsibleChromeHeight, 0)
    }

    var body: some View {
        Group {
            if let error, renderModel?.segments.isEmpty != false {
                ErrorView(message: error) { onRetry() }
                    .padding(.top, pageInsets.layoutTop)
                    .padding(.bottom, screenEdgeInsets.bottom)
                    .accessibilityIdentifier("briefing.lens_error.\(lensKey)")
            } else if let firstRunProgress, renderModel?.segments.isEmpty != false {
                ScrollView {
                    BriefingTierProgressView(progress: firstRunProgress, title: lensTitle)
                        .padding(.top, topContentInset + 24)
                        .padding(.horizontal, Spacing.appHorizontalMargin)
                }
                .contentMargins(.bottom, 40 + screenEdgeInsets.bottom)
                // The bar supplies the status-bar part of the top inset.
                .systemTopScrollEdge(height: screenEdgeInsets.top)
                .refreshable { await onRefresh() }
            } else if let renderModel, renderModel.segments.isEmpty, !renderModel.hasMore {
                BriefingLensEmptyView(
                    lensTitle: lensTitle,
                    topContentInset: topContentInset,
                    screenEdgeInsets: screenEdgeInsets,
                    scrollToTopRequest: scrollToTopRequest,
                    shouldScrollToTop: shouldScrollToTop,
                    onRefresh: onRefresh
                )
            } else if let renderModel {
                let firstSegmentID = renderModel.segments.first?.id
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: segmentSpacing(for: renderModel)) {
                            Color.clear
                                .frame(height: 0)
                                .id(Self.topAnchor)

                            if presentsCardsAsRows {
                                // Lens order, like the cards: no date dividers (B5).
                                ForEach(renderModel.segments) { segmentModel in
                                    VStack(alignment: .leading, spacing: 0) {
                                        if segmentModel.segment.id != firstSegmentID {
                                            KnowledgeTimelineRowDivider()
                                        }
                                        segmentRow(segmentModel, in: renderModel, firstSegmentID: firstSegmentID)
                                    }
                                }
                            } else {
                                ForEach(renderModel.segments) { segmentModel in
                                    segmentRow(segmentModel, in: renderModel, firstSegmentID: firstSegmentID)
                                        .padding(.horizontal, Spacing.appHorizontalMargin)
                                        // Leading within the (centred) reading column.
                                        .frame(maxWidth: readingWidth ?? .infinity, alignment: .leading)
                                        .frame(maxWidth: .infinity)
                                }
                            }

                            if let firstRunProgress, firstRunProgress.processing > 0 || firstRunProgress.pendingSources > 0 || firstRunProgress.failed > 0 {
                                BriefingTierProgressView(progress: firstRunProgress, title: lensTitle)
                            }

                            Color.clear
                                .frame(
                                    height: briefingTrailingReadClearance(
                                        containerHeight: containerHeight,
                                        topContentInset: topContentInset,
                                        readBoundaryY: readBoundaryY
                                    )
                                )
                                .accessibilityHidden(true)

                            if renderModel.hasMore || continuationError != nil {
                                continuationStatus
                                    .padding(.horizontal, Spacing.appHorizontalMargin)
                                    .padding(.bottom, 8)
                            }
                        }
                        .padding(.top, 4)
                        .frame(minHeight: minContentHeight, alignment: .top)
                        .coordinateSpace(name: contentIdentity)
                    }
                    .scrollsToTopOnRequest(
                        scrollToTopRequest,
                        anchor: Self.topAnchor,
                        using: proxy,
                        isEnabled: shouldScrollToTop
                    )
                    .onChange(of: anchorRequest) { _, request in
                        guard let request else { return }
                        proxy.scrollTo(request.segmentID, anchor: .top)
                    }
                    .id(contentIdentity)
                    .contentMargins(.top, pageInsets.contentMarginTop)
                    .contentMargins(.bottom, 40 + screenEdgeInsets.bottom)
                    // The edge effect spans the status bar. The content margin
                    // gives that height back, so the total top inset is
                    // exactly `pageInsets.layoutTop`.
                    .systemTopScrollEdge(height: pageInsets.scrollEdgeBar)
                    .refreshable {
                        await onRefresh()
                    }
                    .onScrollGeometryChange(for: ScrollProbe.self) { geometry in
                        let offset = geometry.contentOffset.y + geometry.contentInsets.top
                        // Pixel-align the chrome to prevent text shimmer. Read
                        // passage uses the raw content offset so it stays in
                        // the same coordinate system as the stored row frames.
                        let scale = max(displayScale, 1)
                        let pixelAligned = (offset * scale).rounded() / scale
                        return ScrollProbe(
                            collapse: min(max(pixelAligned, 0), collapsibleChromeHeight),
                            step: Int((offset / Self.scrollStep).rounded(.down)),
                            containerHeight: geometry.containerSize.height,
                            // Measured from below the status bar, as when the
                            // page frame stopped there, so read passage and the
                            // read boundary stay in the same coordinates.
                            readOffset: geometry.contentOffset.y + pageInsets.readOffsetShift,
                            scrolledDistance: RootTabBarMinimizer.scrolledDistance(in: geometry)
                        )
                    } action: { oldProbe, probe in
                        updateReadViewport(probe)
                        if isReadTrackingEnabled {
                            tabBarMinimizer?.record(offset: probe.scrolledDistance, source: lensKey)
                        }
                        // Before the guard below: once the masthead has fully
                        // collapsed, `collapse` stops changing, but the scroll
                        // still drives the pills.
                        chromeCollapse.trackPinnedChrome(
                            forLens: lensKey,
                            scrolled: probe.scrolledDistance,
                            mastheadHeight: collapsibleChromeHeight,
                            reduceMotion: reduceMotion
                        )
                        guard oldProbe.collapse != probe.collapse
                                || oldProbe.step != probe.step
                                || oldProbe.containerHeight != probe.containerHeight else {
                            return
                        }
                        if probe.step > oldProbe.step, probe.step >= 1 {
                            onScrolledDown()
                        }
                        chromeCollapse.setCollapse(probe.collapse, forLens: lensKey)
                        if probe.containerHeight != containerHeight {
                            containerHeight = probe.containerHeight
                        }
                        let pinned = if probe.collapse >= collapsibleChromeHeight {
                            true
                        } else if probe.collapse <= collapsibleChromeHeight - Self.unpinSlack {
                            false
                        } else {
                            isPinned
                        }
                        if pinned != isPinned {
                            isPinned = pinned
                        }
                        // Unconditional so a recreated pager resyncs stale
                        // view-model pinned state on its initial callback.
                        onSetHeaderPinned(pinned)
                    }
                    .onScrollPhaseChange { _, phase in
                        guard phase == .idle else { return }
                        chromeCollapse.settlePinnedChrome(forLens: lensKey, reduceMotion: reduceMotion)
                    }
                }
                .accessibilityIdentifier("briefing.lens_page.\(lensKey)")
            } else {
                BuddyLoadingView()
                    .padding(.top, pageInsets.layoutTop)
                    .padding(.bottom, screenEdgeInsets.bottom)
                    .onAppear {
                        onLoad()
                    }
            }
        }
        .onChange(of: documentGeneration) { _, _ in
            resetPresentationForDocumentReplacement()
        }
        .onChange(of: readTrackingConfiguration, initial: true) { _, configuration in
            updateReadConfiguration(configuration)
        }
    }

    private func segmentSpacing(for renderModel: BriefingLensRenderModel) -> CGFloat {
        guard renderModel.usesCards else { return 24 }
        return presentsCardsAsRows ? 0 : 20
    }

    private func segmentRow(
        _ segmentModel: BriefingSegmentRenderModel,
        in renderModel: BriefingLensRenderModel,
        firstSegmentID: Int?
    ) -> some View {
        let segment = segmentModel.segment
        return VStack(alignment: .leading, spacing: 16) {
            if renderModel.timelineSeparatorSegmentIDs.contains(segment.id) {
                BriefingTimelineSeparator(date: segment.createdAt)
            }

            segmentView(segmentModel)
                .onAppear {
                    guard segment.id == firstSegmentID,
                          !hasReportedFirstPassage else { return }
                    hasReportedFirstPassage = true
                    onFirstPassageVisible()
                }
                .id(segment.id)
                .onGeometryChange(for: CGRect.self) { proxy in
                    proxy.frame(in: .named(contentIdentity))
                } action: { _, frame in
                    recordSegmentFrame(frame, segmentID: segment.id)
                }
        }
    }

    private func isSelected(_ segmentModel: BriefingSegmentRenderModel) -> Bool {
        guard let selectedSourceKey else { return false }
        return segmentModel.segment.sourceKeys.contains(selectedSourceKey)
    }

    @ViewBuilder
    private func segmentView(_ segmentModel: BriefingSegmentRenderModel) -> some View {
        if let card = segmentModel.card, presentsCardsAsRows {
            BriefingSourceListRow(
                model: segmentModel,
                card: card,
                isSelected: isSelected(segmentModel),
                savedSources: savedSources,
                onOpenSource: onOpenSource,
                onSourceAction: onSourceAction,
                onMarkRead: { onMarkSegmentSeen(segmentModel.segment) }
            )
        } else if let card = segmentModel.card {
            BriefingSourceCardView(
                model: segmentModel,
                card: card,
                savedSources: savedSources,
                onOpenSource: onOpenSource,
                onSourceAction: onSourceAction,
                onOpenDiscussion: { openDiscussion(forSourceKey: $0, in: segmentModel) },
                onDig: onDig,
                onMarkRead: { onMarkSegmentSeen(segmentModel.segment) }
            )
            .overlay {
                // The card open beside News on the open display.
                if isSelected(segmentModel) {
                    RoundedRectangle(cornerRadius: CornerRadius.card, style: .continuous)
                        .strokeBorder(Color.appChromeAccent, lineWidth: 2)
                        .allowsHitTesting(false)
                }
            }
            // One centered column on wide screens; phones fill the margins.
            .frame(maxWidth: Self.maximumCardWidth)
            .frame(maxWidth: .infinity)
        } else {
            BriefingSegmentView(
                model: segmentModel,
                onOpenSource: onOpenSource,
                onSourceAction: onSourceAction,
                onOpenDiscussion: onOpenDiscussion,
                onDig: onDig
            )
        }
    }

    private static let maximumCardWidth: CGFloat = 640

    private func openDiscussion(forSourceKey sourceKey: String, in segmentModel: BriefingSegmentRenderModel) {
        guard let source = segmentModel.sourcesByKey[sourceKey] else { return }
        onOpenDiscussion(source)
    }

    @ViewBuilder
    private var continuationStatus: some View {
        if let continuationError {
            VStack(spacing: 8) {
                Text(continuationError)
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                Button("Retry loading more") {
                    onRetry()
                }
                .buttonStyle(.bordered)
            }
            .frame(maxWidth: .infinity)
            .accessibilityIdentifier("briefing.lens_continuation_error.\(lensKey)")
        } else if isLoadingContinuation {
            ProgressView()
                .frame(maxWidth: .infinity)
                .accessibilityLabel("Loading more briefing stories")
                .accessibilityIdentifier("briefing.lens_continuation_loading.\(lensKey)")
        }
    }

    private func recordSegmentFrame(_ frame: CGRect, segmentID: Int) {
        markSegmentsSeen(
            readTracker.updateFrame(
                frame,
                for: segmentID,
                documentGeneration: documentGeneration
            )
        )
    }

    private func updateReadViewport(_ probe: ScrollProbe) {
        markSegmentsSeen(
            readTracker.updateViewport(
                scrollOffset: probe.readOffset,
                containerHeight: probe.containerHeight,
                documentGeneration: documentGeneration
            )
        )
    }

    private func updateReadConfiguration(
        _ configuration: BriefingReadTrackingConfiguration
    ) {
        markSegmentsSeen(readTracker.updateConfiguration(configuration))
    }

    private func resetPresentationForDocumentReplacement() {
        hasReportedFirstPassage = false
        chromeCollapse.resetCollapse(forLens: lensKey)
        isPinned = false
        onSetHeaderPinned(false)
    }

    private func markSegmentsSeen(_ segmentIDs: [Int]) {
        guard !segmentIDs.isEmpty, let renderModel else { return }
        let segmentsByID = Dictionary(
            uniqueKeysWithValues: renderModel.segments.map { ($0.id, $0.segment) }
        )
        for segmentID in segmentIDs {
            if let segment = segmentsByID[segmentID] {
                onMarkSegmentSeen(segment)
            }
        }
    }
}

struct BriefingTimelineStamp: Equatable {
    let day: String
    let time: String

    static func make(
        for date: Date,
        now: Date = AppClock.now,
        calendar: Calendar = .current,
        locale: Locale = .autoupdatingCurrent,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> BriefingTimelineStamp {
        BriefingTimelineStamp(
            day: TimelineDayLabel.text(for: date, now: now, calendar: calendar),
            time: timeLabel(for: date, locale: locale, timeZone: timeZone)
        )
    }

    private static func timeLabel(
        for date: Date,
        locale: Locale,
        timeZone: TimeZone
    ) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.timeStyle = .short
        formatter.dateStyle = .none
        return formatter.string(from: date)
    }
}

enum BriefingTimelineSeparatorPolicy {
    static let minimumInterval: TimeInterval = 4 * 60 * 60

    static func separatorIndices(for dates: [Date]) -> Set<Int> {
        guard let firstDate = dates.first else { return [] }

        var anchorDate = firstDate
        var result = Set<Int>()
        for (index, date) in dates.enumerated().dropFirst() {
            guard anchorDate.timeIntervalSince(date) >= minimumInterval else { continue }
            result.insert(index)
            anchorDate = date
        }
        return result
    }
}

private struct BriefingTimelineSeparator: View {
    private let stamp: BriefingTimelineStamp

    init(date: Date) {
        self.stamp = .make(for: date)
    }

    var body: some View {
        HStack(spacing: 10) {
            Text(stamp.day)
                .kicker(color: .sectionDelimiter)

            Rectangle()
                .fill(Color.outlineVariant)
                .frame(height: 1)

            Text(stamp.time)
                .monospacedDigit()
                .kicker(color: .sectionDelimiter)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(stamp.day), \(stamp.time)")
    }
}

/// News segments: compact roundup prose with inline source and discussion links.
private struct BriefingSegmentView: View {
    let model: BriefingSegmentRenderModel
    let onOpenSource: (String) -> Void
    var onSourceAction: ((String, BriefingSourceAction) -> Void)? = nil
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(model.passageBlockIndices, id: \.self) { index in
                if let content = model.passageContentByBlockIndex[index] {
                    let opacity = readOpacity(for: model.segment.blocks[index].briefingFallbackReadSourceKeys)
                    BriefingPassageView(
                        content: content,
                        onOpenSource: onOpenSource,
                        onOpenDiscussion: openDiscussion(forSourceKey:),
                        onSourceAction: onSourceAction,
                        onDig: onDig
                    )
                    .opacity(opacity)
                    .animation(.easeInOut(duration: 0.35), value: opacity)
                }
            }
        }
        .padding(.vertical, 2)
        // Read segments recede without losing legibility.
        .opacity(model.allSourcesRead ? 0.72 : 1)
        .animation(.easeInOut(duration: 0.35), value: model.allSourcesRead)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("briefing.segment.\(model.segment.id)")
    }

    private func openDiscussion(forSourceKey sourceKey: String) {
        guard let source = model.sourcesByKey[sourceKey] else { return }
        onOpenDiscussion(source)
    }

    private func readOpacity(for sourceKeys: [String]) -> Double {
        guard !model.allSourcesRead, !sourceKeys.isEmpty else { return 1 }
        return sourceKeys.allSatisfy { model.sourcesByKey[$0]?.read ?? true } ? 0.72 : 1
    }
}


struct BriefingTierProgressView: View {
    let progress: APIBriefingFirstRunTierProgress
    let title: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.appTitle3).foregroundStyle(Color.onSurface)
            if !progress.sourceNames.isEmpty {
                Text(progress.sourceNames.joined(separator: " · "))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            if progress.sourceCount == 0 {
                Text("Add a source to start reading here.")
                NavigationLink("Manage sources") { SettingsView() }
            } else if progress.pendingSources > 0 && progress.discovered == 0 {
                Text("Checking your sources for recent \(title.lowercased()).")
            } else if progress.discovered == 0 && progress.pendingSources == 0 && progress.unavailableSources == 0 {
                Text("Your sources have no recent items yet.")
            } else {
                Text("\(progress.ready) ready. Preparing \(progress.processing) more.")
                if progress.pendingSources > 0 {
                    Text("Still checking \(progress.pendingSources) \(progress.pendingSources == 1 ? "source" : "sources").")
                }
            }
            if progress.failed > 0 || progress.unavailableSources > 0 {
                Text("Some items could not be prepared. Pull to refresh for updates.")
            }
            if progress.skipped > 0 {
                Text("\(progress.skipped) items were skipped.")
            }
        }
        .font(.appBody)
        .foregroundStyle(Color.onSurfaceSecondary)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("briefing.tier_progress.\(progress.tier)")
    }
}

/// Asks a lens page to bring a segment to the top, for example the passage a
/// reader opened when News reflows beside the story. The generation makes a
/// repeat request for the same segment fire again.
struct BriefingScrollAnchorRequest: Equatable {
    let segmentID: Int
    let generation: Int
}
