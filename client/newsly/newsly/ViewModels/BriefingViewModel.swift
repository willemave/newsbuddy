import Foundation
import OSLog
import SwiftUI

private let briefingReadFlushDebounceNanoseconds: UInt64 = 300_000_000
private let briefingReadFlushRetryNanoseconds: UInt64 = 2_000_000_000
private let briefingRefreshLogger = Logger(subsystem: "com.newsly", category: "BriefingRefresh")

protocol BriefingAudioEpisodeServicing: AnyObject {
    func waitForCompletedEpisode(
        _ episode: AudioEpisode,
        pollIntervalNanoseconds: UInt64,
        maxAttempts: Int
    ) async throws -> AudioEpisode
    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource
}

extension AudioEpisodeService: BriefingAudioEpisodeServicing {}

@MainActor
final class BriefingViewModel: ObservableObject {
    private enum TaskKey: Hashable {
        case lens(String)
        case readFlush
        case snapshotSave
    }

    private enum NarrationPreparationOutcome {
        case ready(AudioEpisode)
        case failed(Error, cachedEpisode: AudioEpisode?)
    }

    private struct NarrationPreparation {
        let task: Task<Void, Never>
        var waiters: [UUID: CheckedContinuation<AudioEpisode, Error>]
    }

    enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case empty
        case error(String)
    }

    typealias RefreshPhase = BriefingIndexSynchronizer.RefreshPhase

    @Published private(set) var index: APIBriefingIndexResponse?
    @Published private(set) var orderedLenses: [APIBriefingLensSummary] = []
    @Published private(set) var lenses: [String: APIBriefingLensResponse] = [:]
    @Published private(set) var state: LoadState = .idle
    @Published private(set) var refreshPhase: RefreshPhase = .idle
    @Published private(set) var lensErrors: [String: String] = [:]
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
    private let audioEpisodeService: any BriefingAudioEpisodeServicing
    private let snapshotStore: BriefingSnapshotStoring?
    private let indexSynchronizer: BriefingIndexSynchronizer
    private let tasks = TaskBag<TaskKey>()
    private var pendingReadKeys: Set<String> = []
    private var staleLensKeys: Set<String> = []
    private var headerPinnedLensKeys: Set<String> = []
    private var narrationPreparations: [String: NarrationPreparation] = [:]
    /// The news category to return to when the reader re-enters the news tier.
    private var lastNewsLensKey: String?
    /// Keeps the category strip open after an explicit News tap while the
    /// masthead is compact; cleared on the next scroll-down.
    private var categoryStripPinnedOpen = false

    init(
        service: BriefingServicing,
        audioEpisodeService: any BriefingAudioEpisodeServicing,
        snapshotStore: BriefingSnapshotStoring? = nil,
        refreshPollDelays: [UInt64] = briefingRefreshPollDelaysNanoseconds
    ) {
        self.service = service
        self.audioEpisodeService = audioEpisodeService
        self.snapshotStore = snapshotStore
        let indexSynchronizer = BriefingIndexSynchronizer(
            service: service,
            refreshPollDelays: refreshPollDelays
        )
        self.indexSynchronizer = indexSynchronizer
        indexSynchronizer.onRefreshPhaseChange = { [weak self] phase in
            self?.refreshPhase = phase
        }
    }

    deinit {
        tasks.cancelAll()
        for preparation in narrationPreparations.values {
            preparation.task.cancel()
            for continuation in preparation.waiters.values {
                continuation.resume(throwing: CancellationError())
            }
        }
    }

    var selectedLens: APIBriefingLensResponse? {
        guard let selectedLensKey else { return nil }
        return lenses[selectedLensKey]
    }

    var isRefreshing: Bool {
        refreshPhase == .requesting || refreshPhase == .waitingForVersion
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

    func setActive(_ active: Bool) {
        indexSynchronizer.setActive(active) { [weak self] in
            await self?.loadIndexIfNeeded()
        }
        if !active {
            cancelLensLoads()
        }
    }

    func loadIndexIfNeeded() async {
        guard index == nil else {
            await refreshIndex()
            return
        }
        // Cold start: paint the last briefing immediately, then revalidate
        // against the server via ETag; a version bump refetches the lenses.
        if await restoreFromSnapshot() {
            await refreshIndex()
            return
        }
        await loadIndex(force: true)
    }

    func refreshIndex() async {
        await loadIndex(force: false)
    }

    func pullToRefresh() async {
        await indexSynchronizer.refresh(
            prepare: { [weak self] in
                self?.cancelBackgroundLensLoads()
                self?.tasks.cancel(.readFlush)
                await self?.flushPendingReadMarks()
            },
            onIndexResult: { [weak self] result in
                self?.applyIndexResult(result)
            }
        )
    }

    func selectLens(key: String) {
        guard selectedLensKey != key else { return }
        selectedLensKey = key
        noteSelectionChanged()
        loadWorkingSet()
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
        guard (lenses[key] == nil || staleLensKeys.contains(key)),
              !tasks.isRunning(.lens(key)) else {
            return
        }
        lensErrors.removeValue(forKey: key)
        let expectedVersion = index?.version
        tasks.runReplacing(.lens(key)) { [weak self] token in
            guard let self else { return }
            let startedAt = Date()
            do {
                let response = try await service.fetchLens(key: key)
                guard self.tasks.isCurrent(token), !Task.isCancelled else {
                    return
                }
                guard response.version == expectedVersion,
                      response.version == self.index?.version else {
                    if !Task.isCancelled {
                        await self.refreshIndex()
                    }
                    return
                }
                self.lenses[key] = response
                self.staleLensKeys.remove(key)
                self.lensErrors.removeValue(forKey: key)
                briefingRefreshLogger.info(
                    "Lens loaded | key=\(key, privacy: .public) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public) version=\(response.version, privacy: .public) foreground=\(key == self.selectedLensKey, privacy: .public)"
                )
                self.scheduleSnapshotSave()
            } catch {
                guard !isNetworkCancellation(error) else { return }
                if key == self.selectedLensKey, self.lenses[key] == nil {
                    self.lensErrors[key] = error.localizedDescription
                }
            }
        }
    }

    func retryLens(key: String) {
        lensErrors.removeValue(forKey: key)
        tasks.cancel(.lens(key))
        loadLensIfNeeded(key: key)
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
        scheduleReadFlush()
    }

    func narrationEpisode(for lensKey: String) -> AudioEpisode? {
        narrationEpisodes[lensKey]
    }

    func prepareNarration(for lensKey: String) async throws -> AudioEpisode {
        let waiterID = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                if Task.isCancelled {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                registerNarrationPreparationWaiter(
                    continuation,
                    id: waiterID,
                    for: lensKey
                )
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.cancelNarrationPreparationWaiter(id: waiterID, for: lensKey)
            }
        }
    }

    private func registerNarrationPreparationWaiter(
        _ continuation: CheckedContinuation<AudioEpisode, Error>,
        id waiterID: UUID,
        for lensKey: String
    ) {
        if var preparation = narrationPreparations[lensKey] {
            preparation.waiters[waiterID] = continuation
            narrationPreparations[lensKey] = preparation
            return
        }

        let cachedEpisode = narrationEpisodes[lensKey]
        let briefingService = service
        let audioEpisodeService = audioEpisodeService
        let task = Task { @MainActor [weak self] in
            let outcome = await Self.prepareNarration(
                for: lensKey,
                cachedEpisode: cachedEpisode,
                briefingService: briefingService,
                audioEpisodeService: audioEpisodeService
            )
            self?.finishNarrationPreparation(outcome, for: lensKey)
        }
        narrationPreparations[lensKey] = NarrationPreparation(
            task: task,
            waiters: [waiterID: continuation]
        )
    }

    func narrationStreamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        try await audioEpisodeService.streamResource(for: episode)
    }

    private static func prepareNarration(
        for lensKey: String,
        cachedEpisode: AudioEpisode?,
        briefingService: any BriefingServicing,
        audioEpisodeService: any BriefingAudioEpisodeServicing
    ) async -> NarrationPreparationOutcome {
        do {
            var current: AudioEpisode
            if let cachedEpisode, cachedEpisode.isCompleted {
                return .ready(cachedEpisode)
            } else if let cachedEpisode, cachedEpisode.isGenerating {
                current = cachedEpisode
            } else {
                current = try await briefingService.requestNarration(lensKey: lensKey)
                try Task.checkCancellation()
            }

            if current.isGenerating {
                do {
                    current = try await audioEpisodeService.waitForCompletedEpisode(
                        current,
                        pollIntervalNanoseconds: 1_500_000_000,
                        maxAttempts: 120
                    )
                    try Task.checkCancellation()
                } catch let error where isNetworkCancellation(error) {
                    return .failed(error, cachedEpisode: cachedEpisode)
                } catch let error as AudioEpisodeServiceError {
                    switch error {
                    case .generationFailed:
                        return .failed(error, cachedEpisode: nil)
                    case .preparationTimedOut, .missingStreamResource:
                        return .failed(error, cachedEpisode: current)
                    }
                } catch {
                    return .failed(error, cachedEpisode: current)
                }
            }

            guard current.isCompleted else {
                return .failed(
                    AudioEpisodeServiceError.generationFailed,
                    cachedEpisode: nil
                )
            }
            return .ready(current)
        } catch let error where isNetworkCancellation(error) {
            return .failed(error, cachedEpisode: cachedEpisode)
        } catch {
            return .failed(error, cachedEpisode: cachedEpisode)
        }
    }

    private func applyNarrationPreparation(
        _ outcome: NarrationPreparationOutcome,
        for lensKey: String
    ) throws -> AudioEpisode {
        switch outcome {
        case .ready(let episode):
            narrationEpisodes[lensKey] = episode
            return episode
        case .failed(let error, let cachedEpisode):
            if let cachedEpisode {
                narrationEpisodes[lensKey] = cachedEpisode
            } else {
                narrationEpisodes.removeValue(forKey: lensKey)
            }
            throw error
        }
    }

    private func finishNarrationPreparation(
        _ outcome: NarrationPreparationOutcome,
        for lensKey: String
    ) {
        guard let preparation = narrationPreparations.removeValue(forKey: lensKey) else {
            return
        }
        do {
            let episode = try applyNarrationPreparation(outcome, for: lensKey)
            for continuation in preparation.waiters.values {
                continuation.resume(returning: episode)
            }
        } catch {
            for continuation in preparation.waiters.values {
                continuation.resume(throwing: error)
            }
        }
    }

    private func cancelNarrationPreparationWaiter(id waiterID: UUID, for lensKey: String) {
        guard var preparation = narrationPreparations[lensKey],
              let continuation = preparation.waiters.removeValue(forKey: waiterID) else {
            return
        }
        continuation.resume(throwing: CancellationError())
        if preparation.waiters.isEmpty {
            narrationPreparations.removeValue(forKey: lensKey)
            preparation.task.cancel()
            return
        }
        narrationPreparations[lensKey] = preparation
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
        if index == nil {
            state = .loading
        }
        do {
            if let result = try await indexSynchronizer.load(force: force) {
                applyIndexResult(result)
            }
        } catch {
            if !isNetworkCancellation(error), orderedLenses.isEmpty {
                state = .error(error.localizedDescription)
            }
        }
    }

    private func applyIndexResult(_ result: BriefingIndexFetchResult) {
        switch result {
        case .notModified:
            state = orderedLenses.isEmpty ? .empty : .loaded
            loadWorkingSet()
        case .value(let response, _):
            applyIndex(response)
        }
    }

    private func applyIndex(_ response: APIBriefingIndexResponse) {
        let versionChanged = index.map { $0.version != response.version } ?? false
        if versionChanged {
            markLensesStale(validLensKeys: Set(response.lenses.map(\.key)))
        } else {
            let validKeys = Set(response.lenses.map(\.key))
            lenses = lenses.filter { validKeys.contains($0.key) }
            staleLensKeys.formIntersection(validKeys)
        }
        index = response
        orderedLenses = Self.sortedLenses(response.lenses)
        state = response.lenses.isEmpty ? .empty : .loaded
        if selectedLensKey == nil
            || !response.lenses.contains(where: { $0.key == selectedLensKey }) {
            selectedLensKey = orderedLenses.first?.key
        }
        noteSelectionChanged()
        loadWorkingSet()
        scheduleSnapshotSave()
    }

    private func loadWorkingSet() {
        let workingSetKeys = workingSetLensKeys()
        let workingSet = Set(workingSetKeys)
        for key in Set(orderedLenses.map(\.key)).union(lenses.keys)
            where !workingSet.contains(key) {
            tasks.cancel(.lens(key))
        }
        for key in workingSetKeys {
            loadLensIfNeeded(key: key)
        }
    }

    private func workingSetLensKeys() -> [String] {
        guard let selectedLensKey else { return [] }
        var keys = [selectedLensKey]
        let pages = pagerLenses
        guard let selectedIndex = pages.firstIndex(where: { $0.key == selectedLensKey }) else {
            return keys
        }
        for neighborIndex in [selectedIndex - 1, selectedIndex + 1]
            where pages.indices.contains(neighborIndex) {
            keys.append(pages[neighborIndex].key)
        }
        return keys
    }

    private func cancelBackgroundLensLoads() {
        let selectedKey = selectedLensKey
        for key in orderedLenses.map(\.key) where key != selectedKey {
            tasks.cancel(.lens(key))
        }
    }

    private func cancelLensLoads() {
        for key in Set(orderedLenses.map(\.key)).union(lenses.keys) {
            tasks.cancel(.lens(key))
        }
    }

    /// Applies the persisted briefing so the tab renders without waiting on
    /// the network. Returns false when there is nothing usable to restore.
    private func restoreFromSnapshot() async -> Bool {
        guard let snapshot = await snapshotStore?.load(),
              !snapshot.index.lenses.isEmpty
        else { return false }
        index = snapshot.index
        orderedLenses = Self.sortedLenses(snapshot.index.lenses)
        lenses = snapshot.lenses
        staleLensKeys.removeAll()
        indexSynchronizer.restore(etag: snapshot.etag)
        state = .loaded
        if let savedKey = snapshot.selectedLensKey,
           snapshot.index.lenses.contains(where: { $0.key == savedKey }) {
            selectedLensKey = savedKey
        } else if selectedLensKey == nil
                    || !snapshot.index.lenses.contains(where: { $0.key == selectedLensKey }) {
            selectedLensKey = orderedLenses.first?.key
        }
        noteSelectionChanged()
        return true
    }

    private func scheduleSnapshotSave() {
        guard let snapshotStore else { return }
        tasks.runReplacing(.snapshotSave) { [weak self] in
            do {
                try await Task.sleep(nanoseconds: 500_000_000)
            } catch {
                return
            }
            guard let self, !Task.isCancelled, let index = self.index else { return }
            let workingSet = Set(self.workingSetLensKeys())
            let snapshotLenses = self.lenses.filter {
                workingSet.contains($0.key) && !self.staleLensKeys.contains($0.key)
            }
            await snapshotStore.save(
                BriefingSnapshot(
                    userID: snapshotStore.userID,
                    index: index,
                    etag: self.indexSynchronizer.etag,
                    selectedLensKey: self.selectedLensKey,
                    lenses: snapshotLenses,
                    savedAt: AppClock.now
                )
            )
        }
    }

    private func markLensesStale(validLensKeys: Set<String>) {
        let oldLensKeys = Set(index?.lenses.map(\.key) ?? [])
        let knownLensKeys = validLensKeys
            .union(oldLensKeys)
            .union(lenses.keys)
            .union(staleLensKeys)
        for key in knownLensKeys {
            tasks.cancel(.lens(key))
        }
        lenses = lenses.filter { validLensKeys.contains($0.key) }
        staleLensKeys = validLensKeys
        lensErrors = lensErrors.filter { validLensKeys.contains($0.key) }
    }

    private func scheduleReadFlush(
        delayNanoseconds: UInt64 = briefingReadFlushDebounceNanoseconds
    ) {
        tasks.runReplacing(.readFlush) { [weak self] in
            do {
                try await Task.sleep(nanoseconds: delayNanoseconds)
            } catch {
                return
            }
            await self?.flushPendingReadMarks()
        }
    }

    private func flushPendingReadMarks() async {
        let keys = Array(pendingReadKeys).sorted()
        pendingReadKeys.removeAll()
        guard !keys.isEmpty else { return }
        do {
            let response = try await service.markRead(sourceKeys: keys)
            indexSynchronizer.cancelIndexLoad()
            markCachedLensesStale(containing: keys)
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
            scheduleReadFlush(delayNanoseconds: briefingReadFlushRetryNanoseconds)
        }
    }

    private func markCachedLensesStale(containing sourceKeys: [String]) {
        let keySet = Set(sourceKeys)
        guard !keySet.isEmpty else { return }
        let affectedLensKeys = lenses.compactMap { lensKey, lens -> String? in
            let lensSourceKeys = lens.segments.flatMap(\.sourceKeys)
            if !keySet.isDisjoint(with: lensSourceKeys) {
                return lensKey
            }
            return nil
        }
        for lensKey in affectedLensKeys {
            tasks.cancel(.lens(lensKey))
            staleLensKeys.insert(lensKey)
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
