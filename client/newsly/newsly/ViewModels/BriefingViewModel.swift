import Foundation
import SwiftUI

@MainActor
final class BriefingViewModel: ObservableObject {
    private enum TaskKey: Hashable {
        case lens(String)
        case readFlush
        case snapshotSave
    }

    enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case empty
        case error(String)
    }

    @Published private(set) var index: APIBriefingIndexResponse?
    @Published private(set) var orderedLenses: [APIBriefingLensSummary] = []
    @Published private(set) var lenses: [String: APIBriefingLensResponse] = [:]
    @Published private(set) var state: LoadState = .idle
    @Published var selectedLensKey: String?
    @Published private(set) var narrationEpisodes: [String: AudioEpisode] = [:]
    /// True while the selected lens is scrolled into reading — the masthead
    /// above the pager collapses to hand the space to the content.
    @Published private(set) var isMastheadCompact = false
    /// True while the news category strip is showing beneath the tier strip.
    /// Expanded at the top of a news page (or after tapping News while
    /// reading); collapses as soon as the reader scrolls down.
    @Published private(set) var isCategoryStripExpanded = false

    private let service: BriefingServicing
    private let snapshotStore: BriefingSnapshotStoring?
    private var etag: String?
    private var indexLoadTask: Task<Void, Never>?
    private var loadedLensKeys: Set<String> = []
    private let tasks = TaskBag<TaskKey>()
    private var pendingReadKeys: Set<String> = []
    private var headerPinnedLensKeys: Set<String> = []
    /// The news category to return to when the reader re-enters the news tier.
    private var lastNewsLensKey: String?
    /// Keeps the category strip open after an explicit News tap while the
    /// masthead is compact; cleared on the next scroll-down.
    private var categoryStripPinnedOpen = false

    init(service: BriefingServicing, snapshotStore: BriefingSnapshotStoring? = nil) {
        self.service = service
        self.snapshotStore = snapshotStore
    }

    deinit {
        tasks.cancelAll()
    }

    var selectedLens: APIBriefingLensResponse? {
        guard let selectedLensKey else { return nil }
        return lenses[selectedLensKey]
    }

    var newsLenses: [APIBriefingLensSummary] {
        orderedLenses.filter { $0.tier == .news }
    }

    /// Podcasts / articles — every non-news lens keeps its own pill.
    var fixedLenses: [APIBriefingLensSummary] {
        orderedLenses.filter { $0.tier != .news }
    }

    var isNewsTierSelected: Bool {
        selectedLensSummary?.tier == .news
    }

    var newsUnreadSourceCount: Int {
        newsLenses.reduce(0) { $0 + $1.unreadSourceCount }
    }

    /// Pages the content pager swipes through: all news categories while the
    /// news tier is active, otherwise just the selected fixed lens — swiping
    /// never crosses from a category into podcasts or articles.
    var pagerLenses: [APIBriefingLensSummary] {
        if isNewsTierSelected {
            return newsLenses
        }
        return selectedLensSummary.map { [$0] } ?? []
    }

    private var selectedLensSummary: APIBriefingLensSummary? {
        orderedLenses.first { $0.key == selectedLensKey }
    }

    func handleTabEntered() {
        Task { await loadIndexIfNeeded() }
    }

    func loadIndexIfNeeded() async {
        guard index == nil else {
            await refreshIndex()
            return
        }
        // Cold start: paint the last briefing immediately, then revalidate
        // against the server via ETag; a version bump refetches the lenses.
        if restoreFromSnapshot() {
            await refreshIndex()
            return
        }
        await loadIndex(force: true)
    }

    func refreshIndex() async {
        await loadIndex(force: false)
    }

    func pullToRefresh() async {
        do {
            _ = try await service.requestRefresh()
            try? await Task.sleep(nanoseconds: 350_000_000)
            await loadIndex(force: true)
        } catch {
            state = .error(error.localizedDescription)
        }
    }

    func selectLens(key: String) {
        guard selectedLensKey != key else { return }
        selectedLensKey = key
        noteSelectionChanged()
        loadLensIfNeeded(key: key)
    }

    /// Tapping the single News pill: return to the last-read category, or the
    /// first one on the first visit, and reveal the category strip.
    func selectNewsTier() {
        guard let targetKey = resolvedNewsLensKey() else { return }
        categoryStripPinnedOpen = true
        selectLens(key: targetKey)
        refreshHeaderChrome()
    }

    func setHeaderPinned(_ pinned: Bool, forLens key: String) {
        if pinned {
            headerPinnedLensKeys.insert(key)
        } else {
            headerPinnedLensKeys.remove(key)
        }
        // Back at the top the strip shows on its own; drop the tap override
        // so the next scroll-down collapses it again.
        if !pinned, key == selectedLensKey {
            categoryStripPinnedOpen = false
        }
        refreshHeaderChrome()
    }

    /// Any downward scroll retires a tap-opened category strip.
    func noteScrolledDown(forLens key: String) {
        guard key == selectedLensKey, categoryStripPinnedOpen else { return }
        categoryStripPinnedOpen = false
        refreshHeaderChrome()
    }

    private func noteSelectionChanged() {
        if selectedLensSummary?.tier == .news {
            lastNewsLensKey = selectedLensKey
        } else {
            categoryStripPinnedOpen = false
        }
        refreshHeaderChrome()
    }

    private func resolvedNewsLensKey() -> String? {
        if let lastNewsLensKey, newsLenses.contains(where: { $0.key == lastNewsLensKey }) {
            return lastNewsLensKey
        }
        return newsLenses.first?.key
    }

    private func refreshHeaderChrome() {
        let compact = selectedLensKey.map(headerPinnedLensKeys.contains) ?? false
        if isMastheadCompact != compact {
            isMastheadCompact = compact
        }
        let expanded = isNewsTierSelected && !newsLenses.isEmpty
            && (!compact || categoryStripPinnedOpen)
        if isCategoryStripExpanded != expanded {
            isCategoryStripExpanded = expanded
        }
    }

    func loadLensIfNeeded(key: String) {
        guard !loadedLensKeys.contains(key), !tasks.isRunning(.lens(key)) else { return }
        tasks.runReplacing(.lens(key)) { [weak self] in
            guard let self else { return }
            do {
                let response = try await service.fetchLens(key: key)
                guard response.version >= (self.index?.version ?? 0) else {
                    return
                }
                self.lenses[key] = response
                self.loadedLensKeys.insert(key)
                self.scheduleSnapshotSave()
            } catch {
                // Background prefetch failures stay silent — the page retries
                // on appear. Only the lens the reader is looking at may take
                // the whole tab into an error state.
                if !isNetworkCancellation(error), key == self.selectedLensKey {
                    self.state = .error(error.localizedDescription)
                }
            }
        }
    }

    func markSegmentSeen(_ segment: APIBriefingSegment) {
        markSourcesSeen(segment.sourceKeys)
    }

    func markSourcesSeen(_ sourceKeys: [String]) {
        let unreadKeys = uniqueBriefingSourceKeys(sourceKeys)
            .filter { source(for: $0)?.read == false }
        guard !unreadKeys.isEmpty else { return }
        // Optimistic: grey-out and chip counters react as the reader scrolls,
        // before the debounced network flush. The server tolerates stale keys,
        // and failed flushes re-queue, so local state stays eventually consistent.
        markSourcesReadLocally(unreadKeys)
        pendingReadKeys.formUnion(unreadKeys)
        tasks.runReplacing(.readFlush) { [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            await self?.flushPendingReadMarks()
        }
    }

    func requestNarration(for lensKey: String) async -> AudioEpisode? {
        do {
            let episode = try await service.requestNarration(lensKey: lensKey)
            narrationEpisodes[lensKey] = episode
            return episode
        } catch {
            state = .error(error.localizedDescription)
            return nil
        }
    }

    func storeNarrationEpisode(_ episode: AudioEpisode, for lensKey: String) {
        narrationEpisodes[lensKey] = episode
    }

    func narrationEpisode(for lensKey: String) -> AudioEpisode? {
        narrationEpisodes[lensKey]
    }

    func source(for sourceKey: String) -> APIBriefingSource? {
        for lens in lenses.values {
            if let source = lens.sources.first(where: { $0.sourceKey == sourceKey }) {
                return source
            }
        }
        return nil
    }

    private func loadIndex(force: Bool) async {
        if let indexLoadTask {
            await indexLoadTask.value
            return
        }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.performIndexLoad(force: force)
        }
        indexLoadTask = task
        await task.value
        indexLoadTask = nil
    }

    private func performIndexLoad(force: Bool) async {
        if index == nil {
            state = .loading
        }
        do {
            let result = try await service.fetchIndex(ifNoneMatch: force ? nil : etag)
            switch result {
            case .notModified:
                state = orderedLenses.isEmpty ? .empty : .loaded
            case .value(let response, let responseEtag):
                guard shouldApply(response, force: force) else {
                    state = orderedLenses.isEmpty ? .empty : .loaded
                    return
                }
                if let current = index, response.version != current.version {
                    invalidateLoadedLenses()
                }
                index = response
                orderedLenses = Self.sortedLenses(response.lenses)
                // A missing ETag header must not discard the last known validator.
                etag = responseEtag ?? etag
                state = response.lenses.isEmpty ? .empty : .loaded
                if selectedLensKey == nil || !response.lenses.contains(where: { $0.key == selectedLensKey }) {
                    selectedLensKey = orderedLenses.first?.key
                }
                noteSelectionChanged()
                if let selectedLensKey {
                    loadLensIfNeeded(key: selectedLensKey)
                }
                prefetchRemainingLenses()
                scheduleSnapshotSave()
            }
        } catch {
            // With restored or previously loaded content on screen, a failed
            // revalidation stays silent instead of replacing the briefing
            // with a full-screen error (e.g. offline cold start).
            if !isNetworkCancellation(error), orderedLenses.isEmpty {
                state = .error(error.localizedDescription)
            }
        }
    }

    // A read-mark can bump the version while an index fetch is in flight; never
    // let the older snapshot overwrite the newer state. Forced loads (initial
    // load, pull-to-refresh) accept the server as truth even if its version is
    // lower — e.g. after the backing state was rebuilt.
    private func shouldApply(_ response: APIBriefingIndexResponse, force: Bool) -> Bool {
        guard !force, let current = index else { return true }
        return response.version >= current.version
    }

    /// Warm every lens in the background so swiping between categories never
    /// shows a loading page. Payloads are small and requests share the
    /// connection, so this is cheaper than it looks.
    private func prefetchRemainingLenses() {
        for lens in orderedLenses where lens.key != selectedLensKey {
            loadLensIfNeeded(key: lens.key)
        }
    }

    /// Applies the persisted briefing so the tab renders without waiting on
    /// the network. Returns false when there is nothing usable to restore.
    private func restoreFromSnapshot() -> Bool {
        guard let snapshot = snapshotStore?.load(),
              !snapshot.index.lenses.isEmpty
        else { return false }
        index = snapshot.index
        orderedLenses = Self.sortedLenses(snapshot.index.lenses)
        lenses = snapshot.lenses
        loadedLensKeys = Set(snapshot.lenses.keys)
        etag = snapshot.etag
        state = .loaded
        if selectedLensKey == nil || !snapshot.index.lenses.contains(where: { $0.key == selectedLensKey }) {
            selectedLensKey = orderedLenses.first?.key
        }
        noteSelectionChanged()
        return true
    }

    private func scheduleSnapshotSave() {
        guard snapshotStore != nil else { return }
        tasks.runReplacing(.snapshotSave) { [weak self] in
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard let self, !Task.isCancelled, let index = self.index else { return }
            self.snapshotStore?.save(
                BriefingSnapshot(
                    index: index,
                    etag: self.etag,
                    lenses: self.lenses,
                    savedAt: AppClock.now
                )
            )
        }
    }

    private func invalidateLoadedLenses() {
        let knownLensKeys = Set(loadedLensKeys)
            .union(lenses.keys)
            .union(index?.lenses.map(\.key) ?? [])
        for key in knownLensKeys {
            tasks.cancel(.lens(key))
        }
        loadedLensKeys.removeAll()
    }

    private func flushPendingReadMarks() async {
        let keys = Array(pendingReadKeys).sorted()
        pendingReadKeys.removeAll()
        guard !keys.isEmpty else { return }
        do {
            let response = try await service.markRead(sourceKeys: keys)
            if var currentIndex = index {
                currentIndex = APIBriefingIndexResponse(
                    version: response.version,
                    mastheadTitle: currentIndex.mastheadTitle,
                    mastheadDeck: currentIndex.mastheadDeck,
                    generatedAt: currentIndex.generatedAt,
                    lenses: currentIndex.lenses
                )
                index = currentIndex
                orderedLenses = Self.sortedLenses(currentIndex.lenses)
            }
            scheduleSnapshotSave()
        } catch {
            pendingReadKeys.formUnion(keys)
        }
    }

    /// Optimistic seen-time update: flip sources to read and rederive lens-chip
    /// unread counts. Read segments stay visible (greyed by the view); the server
    /// retires them and the next index fetch reconciles.
    private func markSourcesReadLocally(_ keys: [String]) {
        let keySet = Set(keys)
        var updatedSummaries: [String: APIBriefingLensSummary] = [:]
        for (lensKey, lens) in lenses {
            let updatedSources = lens.sources.map { source in
                guard keySet.contains(source.sourceKey) else { return source }
                return APIBriefingSource(
                    sourceKey: source.sourceKey,
                    kind: source.kind,
                    id: source.id,
                    title: source.title,
                    summary: source.summary,
                    keyPoints: source.keyPoints,
                    url: source.url,
                    imageUrl: source.imageUrl,
                    thumbnailUrl: source.thumbnailUrl,
                    publishedAt: source.publishedAt,
                    contentType: source.contentType,
                    read: true,
                    discussion: source.discussion
                )
            }
            let readSourceKeys = Set(updatedSources.filter(\.read).map(\.sourceKey))
            let segmentSourceKeys = Set(lens.segments.flatMap(\.sourceKeys))
            let updatedSummary = APIBriefingLensSummary(
                key: lens.lens.key,
                tier: lens.lens.tier,
                title: lens.lens.title,
                deck: lens.lens.deck,
                position: lens.lens.position,
                segmentCount: lens.segments.count,
                unreadSourceCount: segmentSourceKeys.subtracting(readSourceKeys).count
            )
            lenses[lensKey] = APIBriefingLensResponse(
                version: lens.version,
                lens: updatedSummary,
                segments: lens.segments,
                sources: updatedSources
            )
            updatedSummaries[lensKey] = updatedSummary
        }
        if var currentIndex = index {
            currentIndex = APIBriefingIndexResponse(
                version: currentIndex.version,
                mastheadTitle: currentIndex.mastheadTitle,
                mastheadDeck: currentIndex.mastheadDeck,
                generatedAt: currentIndex.generatedAt,
                lenses: currentIndex.lenses.map { updatedSummaries[$0.key] ?? $0 }
            )
            index = currentIndex
            orderedLenses = Self.sortedLenses(currentIndex.lenses)
        }
        scheduleSnapshotSave()
    }

    private static func sortedLenses(_ lenses: [APIBriefingLensSummary]) -> [APIBriefingLensSummary] {
        lenses.sorted { left, right in
            if left.position == right.position {
                return left.key < right.key
            }
            return left.position < right.position
        }
    }
}
