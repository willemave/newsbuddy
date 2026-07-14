import SwiftUI

struct BriefingLensPageView: View {
    @Environment(\.displayScale) private var displayScale

    let lensSummary: APIBriefingLensSummary
    let lens: APIBriefingLensResponse?
    @ObservedObject var viewModel: BriefingViewModel
    var chromeCollapse: BriefingChromeCollapseModel
    let collapsibleChromeHeight: CGFloat
    /// Height of the fully expanded chrome; the scroll content is inset by
    /// this so its first line starts at the chrome's bottom edge and rides it
    /// 1:1 while the chrome collapses.
    let topContentInset: CGFloat
    let onOpenSource: (String) -> Void
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void

    @State private var isPinned = false
    @State private var containerHeight: CGFloat = 0

    // Steps only gate the discrete signals (retiring a tap-opened category
    // strip); the chrome collapse itself follows the clamped offset below.
    private static let scrollStep: CGFloat = 64
    /// Hysteresis so the pinned flag doesn't flicker while the finger rests
    /// exactly on the collapse boundary.
    private static let unpinSlack: CGFloat = 12

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

    private var isReadTrackingEnabled: Bool {
        viewModel.isActive && viewModel.selectedLensKey == lensSummary.key
    }

    var body: some View {
        Group {
            if let lens {
                let timelineSeparatorIndices = BriefingTimelineSeparatorPolicy.separatorIndices(
                    for: lens.segments.map(\.createdAt)
                )
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 24) {
                        let sourcesByKey = Dictionary(
                            lens.sources.map { ($0.sourceKey, $0) },
                            uniquingKeysWith: { current, _ in current }
                        )
                        ForEach(Array(lens.segments.enumerated()), id: \.element.id) { index, segment in
                            VStack(alignment: .leading, spacing: 16) {
                                if timelineSeparatorIndices.contains(index) {
                                    BriefingTimelineSeparator(date: segment.createdAt)
                                }

                                BriefingSegmentView(
                                    segment: segment,
                                    sourcesByKey: sourcesByKey,
                                    onOpenSource: onOpenSource,
                                    onOpenDiscussion: onOpenDiscussion,
                                    onDig: onDig
                                )
                                .id(segment.id)
                                .briefingSegmentReadMarker(
                                    isEnabled: isReadTrackingEnabled,
                                    onMidpointCrossed: {
                                        viewModel.markSegmentSeen(segment)
                                    }
                                )
                            }
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                        }

                        Color.clear
                            .frame(height: 24)
                            .accessibilityHidden(true)
                    }
                    .padding(.top, 4)
                    .frame(minHeight: minContentHeight, alignment: .top)
                }
                .id(
                    BriefingLensContentIdentity(
                        lensKey: lensSummary.key,
                        segmentIDs: lens.segments.map(\.id)
                    )
                )
                .contentMargins(.top, topContentInset)
                .contentMargins(.bottom, 40)
                .bottomScreenEdgeFade(fadeHeight: 32)
                .refreshable {
                    await viewModel.pullToRefresh()
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
                        viewModel.noteScrolledDown(forLens: lensSummary.key)
                    }
                    chromeCollapse.setCollapse(probe.collapse, forLens: lensSummary.key)
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
                    viewModel.setHeaderPinned(pinned, forLens: lensSummary.key)
                }
                .accessibilityIdentifier("briefing.lens_page.\(lensSummary.key)")
            } else if let error = viewModel.lensErrors[lensSummary.key] {
                ErrorView(message: error) {
                    viewModel.retryLens(key: lensSummary.key)
                }
                .padding(.top, topContentInset)
                .accessibilityIdentifier("briefing.lens_error.\(lensSummary.key)")
            } else {
                LoadingView()
                    .padding(.top, topContentInset)
                    .onAppear {
                        viewModel.loadLensIfNeeded(key: lensSummary.key)
                    }
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

private struct BriefingSegmentView: View {
    let segment: APIBriefingSegment
    let sourcesByKey: [String: APIBriefingSource]
    let onOpenSource: (String) -> Void
    let onOpenDiscussion: (APIBriefingSource) -> Void
    let onDig: (String, String) -> Void
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
        onDig: @escaping (String, String) -> Void
    ) {
        self.segment = segment
        self.sourcesByKey = sourcesByKey
        self.onOpenSource = onOpenSource
        self.onOpenDiscussion = onOpenDiscussion
        self.onDig = onDig
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
                        onDig: onDig
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

    @ViewBuilder
    private func blockView(_ block: APIBriefingBlock, blockIndex: Int) -> some View {
        switch block.type {
        case .passage:
            BriefingPassageView(
                block: block,
                discussionChips: discussionChipsByBlockIndex[blockIndex] ?? [:],
                onOpenSource: onOpenSource,
                onOpenDiscussion: openDiscussion(forSourceKey:),
                onDig: onDig
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
        guard !allSourcesRead, !sourceKeys.isEmpty else { return 1 }
        return sourceKeys.allSatisfy { sourcesByKey[$0]?.read ?? true } ? 0.72 : 1
    }

    private func source(for block: APIBriefingBlock) -> APIBriefingSource? {
        block.sourceKey.flatMap { sourcesByKey[$0] }
    }
}

private struct BriefingSegmentReadMarker: ViewModifier {
    let isEnabled: Bool
    let onMidpointCrossed: () -> Void

    @State private var tracker = BriefingMidpointReadTracker()
    @State private var hasCrossedMidpoint = false

    func body(content: Content) -> some View {
        content
            .onGeometryChange(for: Bool.self) { proxy in
                briefingSegmentHasCrossedReadMidpoint(frame: proxy.frame(in: .scrollView))
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
        onMidpointCrossed: @escaping () -> Void
    ) -> some View {
        modifier(
            BriefingSegmentReadMarker(
                isEnabled: isEnabled,
                onMidpointCrossed: onMidpointCrossed
            )
        )
    }
}

private struct BriefingFloatingFigurePassage: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass

    let figure: APIBriefingBlock
    let passage: APIBriefingBlock
    let source: APIBriefingSource?
    var discussionChips: [String: BriefingDiscussionChip] = [:]
    let figureOpacity: Double
    let passageOpacity: Double
    let onOpenSource: (String) -> Void
    var onOpenDiscussion: (String) -> Void = { _ in }
    let onDig: (String, String) -> Void

    var body: some View {
        let metrics = BriefingFigureLayoutPolicy.metrics(for: horizontalSizeClass)
        ZStack(alignment: .topTrailing) {
            BriefingPassageView(
                block: passage,
                floatingExclusionSize: metrics.exclusionSize,
                discussionChips: discussionChips,
                onOpenSource: onOpenSource,
                onOpenDiscussion: onOpenDiscussion,
                onDig: onDig
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
