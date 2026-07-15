import SwiftUI

enum BriefingDisplayBlock: Identifiable {
    case single(Int, APIBriefingBlock)
    case floatingFigure(
        Int,
        figure: APIBriefingBlock,
        passage: APIBriefingBlock,
        passageIndex: Int
    )

    var id: Int {
        switch self {
        case .single(let index, _), .floatingFigure(let index, _, _, _):
            return index
        }
    }
}

final class BriefingSegmentRenderModel: Identifiable {
    let segment: APIBriefingSegment
    let sourcesByKey: [String: APIBriefingSource]
    let displayBlocks: [BriefingDisplayBlock]
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
        self.displayBlocks = Self.displayBlocks(
            for: segment.blocks,
            sourcesByKey: segmentSourcesByKey
        )
        let discussionChipsByBlockIndex = Self.discussionChipsByBlockIndex(
            for: segment.blocks,
            sourcesByKey: segmentSourcesByKey
        )
        self.discussionChipsByBlockIndex = discussionChipsByBlockIndex
        let textBuilder = BriefingAttributedTextBuilder()
        self.passageContentByBlockIndex = Dictionary(
            uniqueKeysWithValues: segment.blocks.enumerated().compactMap { index, block in
                guard block.type == .passage else { return nil }
                return (
                    index,
                    textBuilder.build(
                        paragraphs: block.paragraphs ?? [],
                        weight: block.weight,
                        discussionChips: discussionChipsByBlockIndex[index] ?? [:]
                    )
                )
            }
        )
        self.allSourcesRead = !segment.sourceKeys.isEmpty
            && segment.sourceKeys.allSatisfy { segmentSourcesByKey[$0]?.read ?? true }
    }

    /// Inset figures adjacent to a meaty passage float inside it (text wraps);
    /// everything else renders block-by-block as before.
    private static func displayBlocks(
        for blocks: [APIBriefingBlock],
        sourcesByKey: [String: APIBriefingSource]
    ) -> [BriefingDisplayBlock] {
        var items: [BriefingDisplayBlock] = []
        var index = 0
        while index < blocks.count {
            let block = blocks[index]
            if index + 1 < blocks.count,
               canFloatFigure(block, beside: blocks[index + 1], sourcesByKey: sourcesByKey) {
                items.append(.floatingFigure(
                    index,
                    figure: block,
                    passage: blocks[index + 1],
                    passageIndex: index + 1
                ))
                index += 2
                continue
            }
            if block.type == .passage,
               index + 1 < blocks.count,
               canFloatFigure(blocks[index + 1], beside: block, sourcesByKey: sourcesByKey) {
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

    private static func canFloatFigure(
        _ figure: APIBriefingBlock,
        beside passage: APIBriefingBlock,
        sourcesByKey: [String: APIBriefingSource]
    ) -> Bool {
        figure.type == .figure
            && passage.type == .passage
            && BriefingFigureLayoutPolicy.usesInlineLayout(
                placement: figure.placement,
                hasImage: hasImage(figure, sourcesByKey: sourcesByKey),
                passageTextLength: plainTextLength(of: passage)
            )
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
}

final class BriefingLensRenderModel {
    let hasMore: Bool
    let segments: [BriefingSegmentRenderModel]
    let timelineSeparatorSegmentIDs: Set<Int>

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
        let separatorIndices = BriefingTimelineSeparatorPolicy.separatorIndices(
            for: lens.segments.map(\.createdAt)
        )
        self.timelineSeparatorSegmentIDs = Set(
            separatorIndices.map { lens.segments[$0].id }
        )
    }
}

struct BriefingLensPageView: View, Equatable {
    @Environment(\.displayScale) private var displayScale

    let lensKey: String
    let renderModel: BriefingLensRenderModel?
    let isReadTrackingEnabled: Bool
    let readBoundaryY: CGFloat?
    let documentGeneration: Int
    let error: String?
    let continuationError: String?
    let isLoadingContinuation: Bool
    var chromeCollapse: BriefingChromeCollapseModel
    let collapsibleChromeHeight: CGFloat
    /// Height of the fully expanded chrome; the scroll content is inset by
    /// this so its first line starts at the chrome's bottom edge and rides it
    /// 1:1 while the chrome collapses.
    let topContentInset: CGFloat
    let onOpenSource: (String) -> Void
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void
    let onRefresh: () async -> Void
    let onLoad: () -> Void
    let onRetry: () -> Void
    let onFirstPassageVisible: () -> Void
    let onScrolledDown: () -> Void
    let onMarkSegmentSeen: (APIBriefingSegment) -> Void
    let onSetHeaderPinned: (Bool) -> Void

    @State private var isPinned = false
    @State private var containerHeight: CGFloat = 0
    @State private var hasReportedFirstPassage = false

    // Steps only gate the discrete signals (retiring a tap-opened category
    // strip); the chrome collapse itself follows the clamped offset below.
    private static let scrollStep: CGFloat = 64
    /// Hysteresis so the pinned flag doesn't flicker while the finger rests
    /// exactly on the collapse boundary.
    private static let unpinSlack: CGFloat = 12

    static func == (lhs: BriefingLensPageView, rhs: BriefingLensPageView) -> Bool {
        lhs.lensKey == rhs.lensKey
            && lhs.renderModel === rhs.renderModel
            && lhs.isReadTrackingEnabled == rhs.isReadTrackingEnabled
            && lhs.readBoundaryY == rhs.readBoundaryY
            && lhs.documentGeneration == rhs.documentGeneration
            && lhs.error == rhs.error
            && lhs.continuationError == rhs.continuationError
            && lhs.isLoadingContinuation == rhs.isLoadingContinuation
            && lhs.chromeCollapse === rhs.chromeCollapse
            && lhs.collapsibleChromeHeight == rhs.collapsibleChromeHeight
            && lhs.topContentInset == rhs.topContentInset
    }

    /// The scroll facts the chrome cares about. `collapse` is clamped to the
    /// collapsible chrome height, so it only streams updates during the
    /// collapse window and stays constant while reading below it.
    private struct ScrollProbe: Equatable {
        var collapse: CGFloat = 0
        var step = 0
        var containerHeight: CGFloat = 0
    }

    /// Guarantees every page can scroll far enough to fully collapse the
    /// chrome, so short pages never rest half-collapsed.
    private var minContentHeight: CGFloat? {
        guard containerHeight > 0 else { return nil }
        return max(containerHeight - topContentInset + collapsibleChromeHeight, 0)
    }

    var body: some View {
        Group {
            if let renderModel {
                let firstSegmentID = renderModel.segments.first?.id
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 24) {
                        ForEach(renderModel.segments) { segmentModel in
                            let segment = segmentModel.segment
                            VStack(alignment: .leading, spacing: 16) {
                                if renderModel.timelineSeparatorSegmentIDs.contains(segment.id) {
                                    BriefingTimelineSeparator(date: segment.createdAt)
                                }

                                BriefingSegmentView(
                                    model: segmentModel,
                                    onOpenSource: onOpenSource,
                                    onOpenDiscussion: onOpenDiscussion,
                                    onDig: onDig
                                )
                                .onAppear {
                                    guard segment.id == firstSegmentID,
                                          !hasReportedFirstPassage else { return }
                                    hasReportedFirstPassage = true
                                    onFirstPassageVisible()
                                }
                                .id(segment.id)
                                .briefingSegmentReadMarker(
                                    isEnabled: isReadTrackingEnabled,
                                    readBoundaryY: readBoundaryY,
                                    onMidpointCrossed: {
                                        onMarkSegmentSeen(segment)
                                    }
                                )
                            }
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                        }

                        Color.clear
                            .frame(height: 24)
                            .accessibilityHidden(true)

                        if renderModel.hasMore || continuationError != nil {
                            continuationStatus
                                .padding(.horizontal, Spacing.appHorizontalMargin)
                                .padding(.bottom, 8)
                        }
                    }
                    .padding(.top, 4)
                    .frame(minHeight: minContentHeight, alignment: .top)
                }
                .id(
                    BriefingLensContentIdentity(
                        lensKey: lensKey,
                        generation: documentGeneration
                    )
                )
                .contentMargins(.top, topContentInset)
                .contentMargins(.bottom, 40)
                .bottomScreenEdgeFade(fadeHeight: 32)
                .refreshable {
                    await onRefresh()
                }
                .onScrollGeometryChange(for: ScrollProbe.self) { geometry in
                    let offset = geometry.contentOffset.y + geometry.contentInsets.top
                    // Pixel-align so the chrome never lands on sub-pixel
                    // heights (text shimmer), then clamp to the collapse
                    // window so steady reading streams no updates at all.
                    let scale = max(displayScale, 1)
                    let pixelAligned = (offset * scale).rounded() / scale
                    return ScrollProbe(
                        collapse: min(max(pixelAligned, 0), collapsibleChromeHeight),
                        step: Int((offset / Self.scrollStep).rounded(.down)),
                        containerHeight: geometry.containerSize.height
                    )
                } action: { oldProbe, probe in
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
                .accessibilityIdentifier("briefing.lens_page.\(lensKey)")
            } else if let error {
                ErrorView(message: error) {
                    onRetry()
                }
                .padding(.top, topContentInset)
                .accessibilityIdentifier("briefing.lens_error.\(lensKey)")
            } else {
                LoadingView()
                    .padding(.top, topContentInset)
                    .onAppear {
                        onLoad()
                    }
            }
        }
        .onChange(of: documentGeneration) { _, _ in
            hasReportedFirstPassage = false
        }
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

private struct BriefingSegmentView: View {
    let model: BriefingSegmentRenderModel
    let onOpenSource: (String) -> Void
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(model.displayBlocks) { item in
                switch item {
                case .single(let index, let block):
                    blockView(block, blockIndex: index)
                case .floatingFigure(_, let figure, let passage, let passageIndex):
                    BriefingFloatingFigurePassage(
                        figure: figure,
                        passageContent: model.passageContentByBlockIndex[passageIndex],
                        source: source(for: figure),
                        figureOpacity: readOpacity(for: figure.briefingDirectSourceKeys),
                        passageOpacity: readOpacity(for: passage.briefingFallbackReadSourceKeys),
                        onOpenSource: onOpenSource,
                        onOpenDiscussion: openDiscussion(forSourceKey:),
                        onDig: onDig
                    )
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

    @ViewBuilder
    private func blockView(_ block: APIBriefingBlock, blockIndex: Int) -> some View {
        switch block.type {
        case .passage:
            if let content = model.passageContentByBlockIndex[blockIndex] {
                BriefingPassageView(
                    content: content,
                    onOpenSource: onOpenSource,
                    onOpenDiscussion: openDiscussion(forSourceKey:),
                    onDig: onDig
                )
                .opacity(readOpacity(for: block.briefingFallbackReadSourceKeys))
                .animation(
                    .easeInOut(duration: 0.35),
                    value: readOpacity(for: block.briefingFallbackReadSourceKeys)
                )
            }
        case .figure:
            BriefingFigureView(
                block: block,
                source: source(for: block),
                onOpenSource: onOpenSource
            )
            .opacity(readOpacity(for: block.briefingDirectSourceKeys))
            .animation(.easeInOut(duration: 0.35), value: readOpacity(for: block.briefingDirectSourceKeys))
        case .pullquote:
            BriefingPullquoteView(
                block: block,
                source: source(for: block),
                onOpenSource: onOpenSource
            )
            .opacity(readOpacity(for: block.briefingDirectSourceKeys))
            .animation(.easeInOut(duration: 0.35), value: readOpacity(for: block.briefingDirectSourceKeys))
        }
    }

    private func readOpacity(for sourceKeys: [String]) -> Double {
        guard !model.allSourcesRead, !sourceKeys.isEmpty else { return 1 }
        return sourceKeys.allSatisfy { model.sourcesByKey[$0]?.read ?? true } ? 0.72 : 1
    }

    private func source(for block: APIBriefingBlock) -> APIBriefingSource? {
        block.sourceKey.flatMap { model.sourcesByKey[$0] }
    }
}

private struct BriefingSegmentReadMarker: ViewModifier {
    let isEnabled: Bool
    let readBoundaryY: CGFloat?
    let onMidpointCrossed: () -> Void

    @State private var tracker = BriefingMidpointReadTracker()
    @State private var hasCrossedMidpoint = false

    func body(content: Content) -> some View {
        content
            .onGeometryChange(for: Bool.self) { proxy in
                briefingSegmentHasCrossedReadMidpoint(
                    frame: proxy.frame(in: .named(briefingReadCoordinateSpaceName)),
                    readBoundaryY: readBoundaryY
                )
            } action: { _, crossed in
                hasCrossedMidpoint = crossed
                markReadIfNeeded(hasCrossedMidpoint: crossed, isEnabled: isEnabled)
            }
            .onChange(of: isEnabled, initial: true) { _, enabled in
                markReadIfNeeded(hasCrossedMidpoint: hasCrossedMidpoint, isEnabled: enabled)
            }
    }

    private func markReadIfNeeded(hasCrossedMidpoint: Bool, isEnabled: Bool) {
        guard tracker.update(
            hasCrossedMidpoint: hasCrossedMidpoint,
            isEnabled: isEnabled
        ) else { return }
        onMidpointCrossed()
    }
}

private extension View {
    func briefingSegmentReadMarker(
        isEnabled: Bool,
        readBoundaryY: CGFloat?,
        onMidpointCrossed: @escaping () -> Void
    ) -> some View {
        modifier(
            BriefingSegmentReadMarker(
                isEnabled: isEnabled,
                readBoundaryY: readBoundaryY,
                onMidpointCrossed: onMidpointCrossed
            )
        )
    }
}

private struct BriefingFloatingFigurePassage: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    let figure: APIBriefingBlock
    let passageContent: BriefingAttributedTextBuilder.Result?
    let source: APIBriefingSource?
    let figureOpacity: Double
    let passageOpacity: Double
    let onOpenSource: (String) -> Void
    var onOpenDiscussion: (String) -> Void = { _ in }
    let onDig: (String, String) -> Void

    var body: some View {
        let metrics = BriefingFigureLayoutPolicy.metrics(for: horizontalSizeClass)
        ZStack(alignment: .topTrailing) {
            if let passageContent {
                BriefingPassageView(
                    content: passageContent,
                    floatingExclusionSize: metrics.exclusionSize,
                    onOpenSource: onOpenSource,
                    onOpenDiscussion: onOpenDiscussion,
                    onDig: onDig
                )
                .frame(maxWidth: .infinity, alignment: .leading)
                .opacity(passageOpacity)
                .animation(.easeInOut(duration: 0.35), value: passageOpacity)
            }

            Button {
                if let sourceKey = figure.sourceKey ?? source?.sourceKey {
                    onOpenSource(sourceKey)
                }
            } label: {
                CachedAsyncImage(
                    url: ServerImageURL.resolve(figure.imageUrl ?? source?.imageUrl),
                    thumbnailUrl: ServerImageURL.resolve(figure.thumbnailUrl ?? source?.thumbnailUrl),
                    targetSize: metrics.imageSize
                ) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } placeholder: {
                    Rectangle()
                        .fill(Color.surfaceSecondary)
                }
                .frame(width: metrics.imageSize.width, height: metrics.imageSize.height)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .stroke(Color.primary.opacity(0.1), lineWidth: 1)
                }
            }
            .buttonStyle(.plain)
            .opacity(figureOpacity)
            .animation(.easeInOut(duration: 0.35), value: figureOpacity)
            .accessibilityLabel(source?.title ?? "Article image")
        }
    }
}

private struct BriefingFigureView: View {
    let block: APIBriefingBlock
    let source: APIBriefingSource?
    let onOpenSource: (String) -> Void

    private var figureHeight: CGFloat {
        BriefingFigureLayoutPolicy.canonicalPlacement(block.placement) == .inset ? 176 : 216
    }

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
                    .frame(height: figureHeight)
                    .clipped()
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            } placeholder: {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.surfaceSecondary)
                    .frame(height: figureHeight)
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
