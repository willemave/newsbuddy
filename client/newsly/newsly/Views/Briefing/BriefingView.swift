import MarkdownUI
import SwiftUI

struct BriefingView: View {
    @ObservedObject var viewModel: BriefingViewModel

    @StateObject private var digViewModel = BriefingDigViewModel(service: LiveBriefingService())
    @StateObject private var playbackService = NarrationPlaybackService.shared
    @State private var activeSource: BriefingSourceSheetItem?
    @State private var preparingNarrationLensKeys: Set<String> = []
    @State private var narrationError: String?

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
                contentIds: contentIdsForCurrentLens()
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
        TabView(selection: selectedLensBinding) {
            ForEach(viewModel.orderedLenses, id: \.key) { lens in
                BriefingLensPageView(
                    lensSummary: lens,
                    lens: viewModel.lenses[lens.key],
                    viewModel: viewModel,
                    mastheadDate: viewModel.index?.generatedAt ?? AppClock.now,
                    narrationSection: AnyView(
                        narrationSection(lensKey: lens.key, lensTitle: lens.title)
                    ),
                    onOpenSource: openSource,
                    onDig: startDig
                )
                .tag(lens.key)
            }
        }
        .tabViewStyle(.page(indexDisplayMode: .never))
        .accessibilityIdentifier("briefing.lens_pager")
    }

    @ViewBuilder
    private func narrationSection(lensKey: String, lensTitle: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let narrationError {
                Text(narrationError)
                    .font(.appCaption)
                    .foregroundStyle(Color.statusDestructive)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            BriefingNarrationBar(
                lensTitle: lensTitle,
                episode: viewModel.narrationEpisode(for: lensKey),
                isPreparing: preparingNarrationLensKeys.contains(lensKey),
                playbackService: playbackService,
                onToggle: {
                    Task { await toggleNarration(lensKey: lensKey) }
                }
            )
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
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

    private func startDig(fragment: String, passageContext: String) {
        digViewModel.dig(fragment: fragment, passageContext: passageContext)
    }

    private func contentIdsForCurrentLens() -> [Int] {
        guard let selectedLens = viewModel.selectedLens else { return [] }
        return selectedLens.sources
            .filter { $0.kind == "content" }
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

private struct BriefingLensStrip: View {
    @ObservedObject var viewModel: BriefingViewModel
    let isPinned: Bool

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(viewModel.orderedLenses, id: \.key) { lens in
                    BriefingLensPill(
                        lens: lens,
                        isSelected: lens.key == viewModel.selectedLensKey
                    ) {
                        viewModel.selectLens(key: lens.key)
                    }
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 10)
        }
        .background(Color.surfacePrimary)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.outlineVariant.opacity(isPinned ? 0.5 : 0))
                .frame(height: 0.5)
        }
        .shadow(color: .black.opacity(isPinned ? 0.12 : 0), radius: 10, y: 5)
        .accessibilityIdentifier("briefing.lenses")
    }
}

private struct BriefingLensPill: View {
    let lens: APIBriefingLensSummary
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(lens.title)
                    .font(.appCaption.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)

                if lens.unreadSourceCount > 0 {
                    Text("\(lens.unreadSourceCount)")
                        .font(.appCaption2.weight(.bold).monospacedDigit())
                        .foregroundStyle(isSelected ? Color.surfacePrimary : Color.brandPrimary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(
                            Capsule()
                                .fill(isSelected ? Color.onSurface : Color.brandPrimary.opacity(0.12))
                        )
                }
            }
            .foregroundStyle(isSelected ? Color.surfacePrimary : Color.onSurface)
            .frame(minHeight: 36)
            .padding(.horizontal, 12)
            .background(
                Capsule()
                    .fill(isSelected ? Color.onSurface : Color.surfaceSecondary)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(lens.title), \(lens.unreadSourceCount) unread sources")
        .accessibilityIdentifier("briefing.lens.\(lens.key)")
    }
}

private struct BriefingLensPageView: View {
    let lensSummary: APIBriefingLensSummary
    let lens: APIBriefingLensResponse?
    @ObservedObject var viewModel: BriefingViewModel
    let mastheadDate: Date
    let narrationSection: AnyView
    let onOpenSource: (String) -> Void
    let onDig: (String, String) -> Void

    @State private var topVisibleSegmentId: Int?
    @State private var isHeaderPinned = false

    private var pinnedHeaderId: String { "briefing.strip.\(lensSummary.key)" }

    var body: some View {
        Group {
            if let lens {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 24, pinnedViews: [.sectionHeaders]) {
                            EditorialMastheadHeader(title: "Briefing", date: mastheadDate)
                                .padding(.bottom, -20)

                            Section {
                                narrationSection

                                lensHeader(lens.lens)

                                ForEach(lens.segments, id: \.id) { segment in
                                    BriefingSegmentView(
                                        segment: segment,
                                        sourceLookup: { viewModel.source(for: $0) },
                                        onOpenSource: onOpenSource,
                                        onDig: onDig
                                    )
                                    .id(segment.id)
                                    .padding(.horizontal, Spacing.appHorizontalMargin)
                                }

                                Color.clear
                                    .frame(height: 24)
                                    .accessibilityHidden(true)
                            } header: {
                                BriefingLensStrip(viewModel: viewModel, isPinned: isHeaderPinned)
                                    .id(pinnedHeaderId)
                            }
                        }
                        .scrollTargetLayout()
                        .padding(.top, 4)
                    }
                    .refreshable {
                        await viewModel.pullToRefresh()
                    }
                    .onScrollTargetVisibilityChange(idType: Int.self) { visibleIds in
                        handleVisibleSegments(visibleIds, segments: lens.segments)
                    }
                    .onScrollGeometryChange(for: Bool.self) { geometry in
                        geometry.contentOffset.y + geometry.contentInsets.top > 96
                    } action: { _, pinned in
                        viewModel.setHeaderPinned(pinned, forLens: lensSummary.key)
                        withAnimation(.easeInOut(duration: 0.22)) {
                            isHeaderPinned = pinned
                        }
                    }
                    .onChange(of: viewModel.selectedLensKey, initial: true) { _, newValue in
                        // Arriving from a page whose strip was pinned: start pinned
                        // here too, unless the reader was already deeper in this page.
                        // `initial: true` covers pages the pager creates lazily after
                        // the selection has already changed.
                        guard newValue == lensSummary.key,
                              viewModel.carryHeaderPinned,
                              !isHeaderPinned
                        else { return }
                        proxy.scrollTo(pinnedHeaderId, anchor: .top)
                    }
                    .accessibilityIdentifier("briefing.lens_page.\(lensSummary.key)")
                }
            } else {
                LoadingView()
                    .onAppear {
                        viewModel.loadLensIfNeeded(key: lensSummary.key)
                    }
            }
        }
    }

    private func lensHeader(_ lens: APIBriefingLensSummary) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(tierLabel(lens.tier))
                .kicker()
            Text(lens.deck)
                .font(.appCallout)
                .foregroundStyle(Color.onSurfaceSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.bottom, 2)
    }

    private func handleVisibleSegments(_ visibleIds: [Int], segments: [APIBriefingSegment]) {
        let previousTop = topVisibleSegmentId
        topVisibleSegmentId = visibleIds.first
        guard let previousTop,
              !visibleIds.contains(previousTop),
              let segment = segments.first(where: { $0.id == previousTop })
        else { return }
        viewModel.markSegmentSeen(segment)
    }

    private func tierLabel(_ tier: APIBriefingTier) -> String {
        switch tier {
        case .audio:
            return "AUDIO"
        case .longform:
            return "LONG READ"
        case .news:
            return "FAST READ"
        }
    }
}

private struct BriefingSegmentView: View {
    let segment: APIBriefingSegment
    let sourceLookup: (String) -> APIBriefingSource?
    let onOpenSource: (String) -> Void
    let onDig: (String, String) -> Void

    private enum DisplayBlock: Identifiable {
        case single(Int, APIBriefingBlock)
        case floatingFigure(Int, figure: APIBriefingBlock, passage: APIBriefingBlock)

        var id: Int {
            switch self {
            case .single(let index, _), .floatingFigure(let index, _, _):
                return index
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(displayBlocks) { item in
                switch item {
                case .single(_, let block):
                    blockView(block)
                case .floatingFigure(_, let figure, let passage):
                    BriefingFloatingFigurePassage(
                        figure: figure,
                        passage: passage,
                        source: figure.sourceKey.flatMap(sourceLookup),
                        onOpenSource: onOpenSource,
                        onDig: onDig
                    )
                }
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("briefing.segment.\(segment.id)")
    }

    /// Inset figures adjacent to a meaty passage float inside it (text wraps);
    /// everything else renders block-by-block as before.
    private var displayBlocks: [DisplayBlock] {
        let blocks = segment.blocks
        var items: [DisplayBlock] = []
        var index = 0
        while index < blocks.count {
            let block = blocks[index]
            if isFloatableFigure(block),
               index + 1 < blocks.count,
               isFloatHost(blocks[index + 1]) {
                items.append(.floatingFigure(index, figure: block, passage: blocks[index + 1]))
                index += 2
                continue
            }
            if isFloatHost(block),
               index + 1 < blocks.count,
               isFloatableFigure(blocks[index + 1]) {
                items.append(.floatingFigure(index, figure: blocks[index + 1], passage: block))
                index += 2
                continue
            }
            items.append(.single(index, block))
            index += 1
        }
        return items
    }

    private func isFloatableFigure(_ block: APIBriefingBlock) -> Bool {
        block.type == .figure && block.placement == "inset" && hasImage(block)
    }

    private func isFloatHost(_ block: APIBriefingBlock) -> Bool {
        block.type == .passage && plainTextLength(of: block) >= 240
    }

    private func plainTextLength(of block: APIBriefingBlock) -> Int {
        (block.paragraphs ?? []).reduce(0) { total, paragraph in
            total + paragraph.runs.reduce(0) { $0 + $1.text.count }
        }
    }

    private func hasImage(_ block: APIBriefingBlock) -> Bool {
        let source = block.sourceKey.flatMap(sourceLookup)
        let url = block.imageUrl ?? block.thumbnailUrl ?? source?.imageUrl ?? source?.thumbnailUrl
        return url?.isEmpty == false
    }

    @ViewBuilder
    private func blockView(_ block: APIBriefingBlock) -> some View {
        switch block.type {
        case .passage:
            BriefingPassageView(
                block: block,
                onOpenSource: onOpenSource,
                onDig: onDig
            )
        case .figure:
            BriefingFigureView(
                block: block,
                source: block.sourceKey.flatMap(sourceLookup),
                onOpenSource: onOpenSource
            )
        case .pullquote:
            BriefingPullquoteView(
                block: block,
                source: block.sourceKey.flatMap(sourceLookup),
                onOpenSource: onOpenSource
            )
        }
    }
}

private struct BriefingFloatingFigurePassage: View {
    let figure: APIBriefingBlock
    let passage: APIBriefingBlock
    let source: APIBriefingSource?
    let onOpenSource: (String) -> Void
    let onDig: (String, String) -> Void

    private static let imageSize = CGSize(width: 148, height: 148)
    // Exclusion adds the text gutter around the floated image.
    private static let exclusionSize = CGSize(width: 162, height: 160)

    var body: some View {
        ZStack(alignment: .topTrailing) {
            BriefingPassageView(
                block: passage,
                floatingExclusionSize: Self.exclusionSize,
                onOpenSource: onOpenSource,
                onDig: onDig
            )
            .frame(maxWidth: .infinity, alignment: .leading)

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

private struct BriefingNarrationBar: View {
    let lensTitle: String
    let episode: AudioEpisode?
    let isPreparing: Bool
    @ObservedObject var playbackService: NarrationPlaybackService
    let onToggle: () -> Void

    private var target: NarrationTarget? {
        episode.map { .audioEpisode($0.id) }
    }

    private var shouldShowControls: Bool {
        guard let target else { return isPreparing }
        return isPreparing || target == playbackService.speakingTarget
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Button(action: onToggle) {
                    Image(systemName: playbackIconName)
                        .font(.appSymbol(size: 14, weight: .bold))
                        .foregroundStyle(Color.surfacePrimary)
                        .frame(width: 36, height: 36)
                        .background(Circle().fill(Color.brandPrimary))
                }
                .buttonStyle(.plain)
                .disabled(isPreparing)
                .accessibilityLabel(playbackLabel)
                .accessibilityIdentifier("briefing.narration.play")

                VStack(alignment: .leading, spacing: 2) {
                    Text("Listen")
                        .font(.appCaption.weight(.semibold))
                        .foregroundStyle(Color.onSurface)
                    Text(lensTitle)
                        .font(.appCaption2)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 8)
            }

            if shouldShowControls {
                NarrationPlaybackControlRow(
                    playbackService: playbackService,
                    target: target,
                    isPreparing: isPreparing,
                    cornerRadius: 8,
                    onTogglePlayback: onToggle
                )
            }
        }
        .padding(10)
        .background(Color.surfacePrimary.opacity(0.94))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
        }
    }

    private var playbackIconName: String {
        if isPreparing {
            return "hourglass"
        }
        if let target,
           playbackService.speakingTarget == target,
           playbackService.isSpeaking {
            return "pause.fill"
        }
        return "play.fill"
    }

    private var playbackLabel: String {
        if isPreparing {
            return "Preparing briefing audio"
        }
        if let target,
           playbackService.speakingTarget == target,
           playbackService.isSpeaking {
            return "Pause briefing audio"
        }
        return "Play briefing audio"
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

private struct BriefingSourceSheet: View {
    let item: BriefingSourceSheetItem
    let contentIds: [Int]

    @Environment(\.dismiss) private var dismiss
    @State private var safariItem: BriefingSafariItem?

    var body: some View {
        sourceContent
            .sheet(item: $safariItem) { item in
                SafariView(url: item.url)
                    .ignoresSafeArea()
            }
    }

    @ViewBuilder
    private var sourceContent: some View {
        if item.source.kind == "content" {
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
        } else {
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let imageURL = ServerImageURL.resolve(item.source.imageUrl ?? item.source.thumbnailUrl) {
                            CachedAsyncImage(url: imageURL) { image in
                                image
                                    .resizable()
                                    .aspectRatio(contentMode: .fill)
                                    .frame(maxWidth: .infinity)
                                    .frame(height: 220)
                                    .clipped()
                                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                            } placeholder: {
                                RoundedRectangle(cornerRadius: 8, style: .continuous)
                                    .fill(Color.surfaceSecondary)
                                    .frame(height: 220)
                            }
                        }

                        VStack(alignment: .leading, spacing: 8) {
                            Text("Source")
                                .kicker()
                            Text(item.source.title)
                                .font(.appTitle3)
                                .foregroundStyle(Color.onSurface)
                                .fixedSize(horizontal: false, vertical: true)
                            if let summary = item.source.summary {
                                Text(summary)
                                    .font(.appCallout)
                                    .foregroundStyle(Color.onSurfaceSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }

                        if let keyPoints = item.source.keyPoints, !keyPoints.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("Key Points")
                                    .kicker()
                                ForEach(keyPoints, id: \.self) { point in
                                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                                        Circle()
                                            .fill(Color.brandPrimary)
                                            .frame(width: 5, height: 5)
                                        Text(point)
                                            .font(.appCallout)
                                            .foregroundStyle(Color.onSurface)
                                    }
                                }
                            }
                        }

                        if let rawURL = item.source.url,
                           let url = URL(string: rawURL) {
                            Button {
                                safariItem = BriefingSafariItem(url: url)
                            } label: {
                                Label("Open original", systemImage: "safari")
                                    .font(.appCallout.weight(.semibold))
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(Color.brandPrimary)
                            .padding(.top, 4)
                        }
                    }
                    .padding(Spacing.appHorizontalMargin)
                }
                .background(Color.surfacePrimary)
                .navigationTitle("Briefing Source")
                .navigationBarTitleDisplayMode(.inline)
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
}
