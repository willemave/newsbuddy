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
                            // Segment-level backstop: sources are only seen
                            // after the whole segment has moved above the
                            // viewport, unless their own block exited first.
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
                    .frame(minHeight: minContentHeight, alignment: .top)
                }
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

    @State private var markedSourceKeys = Set<String>()

    var body: some View {
        BriefingPassageView(
            block: block,
            floatingExclusionSize: floatingExclusionSize,
            discussionChips: discussionChips,
            onOpenSource: onOpenSource,
            onOpenDiscussion: onOpenDiscussion,
            onDig: onDig
        )
        .onGeometryChange(for: [String].self) { proxy in
            proxy.frame(in: .scrollView).maxY < 0 ? block.briefingFallbackReadSourceKeys : []
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
    let onSourceKeysSeen: ([String]) -> Void

    private static let imageSize = CGSize(width: 148, height: 148)
    // Exclusion adds the text gutter around the floated image.
    private static let exclusionSize = CGSize(width: 162, height: 160)

    @ViewBuilder
    var body: some View {
        if horizontalSizeClass == .compact {
            stackedLayout
        } else {
            floatingLayout
        }
    }

    private var stackedLayout: some View {
        VStack(alignment: .leading, spacing: 12) {
            BriefingFigureView(
                block: figure,
                source: source,
                onOpenSource: onOpenSource
            )
            .opacity(figureOpacity)
            .animation(.easeInOut(duration: 0.35), value: figureOpacity)
            .briefingSourceReadMarker(
                sourceKeys: figure.briefingDirectSourceKeys,
                onSourceKeysSeen: onSourceKeysSeen
            )

            BriefingPassageReadMarker(
                block: passage,
                discussionChips: discussionChips,
                onOpenSource: onOpenSource,
                onOpenDiscussion: onOpenDiscussion,
                onDig: onDig,
                onSourceKeysSeen: onSourceKeysSeen
            )
            .opacity(passageOpacity)
            .animation(.easeInOut(duration: 0.35), value: passageOpacity)
        }
    }

    private var floatingLayout: some View {
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
            .accessibilityLabel(source?.title ?? "Article image")
        }
        .briefingSourceReadMarker(
            sourceKeys: figure.briefingDirectSourceKeys,
            onSourceKeysSeen: onSourceKeysSeen
        )
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
