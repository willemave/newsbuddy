import Foundation
import Observation

private let briefingReadFlushDebounceNanoseconds: UInt64 = 300_000_000
private let briefingReadFlushRetryNanoseconds: UInt64 = 2_000_000_000
private let briefingFirstRunCompletionRetryNanoseconds: UInt64 = 2_000_000_000
private let briefingIndexFreshnessInterval: TimeInterval = 15 * 60
private let briefingInitialIndexRetryDelaysNanoseconds: [UInt64] = [
    250_000_000,
    750_000_000,
]
private let briefingRefreshLogger = BriefingPerformance.logger

private enum BriefingDestination: Equatable {
    case startHere
    case lens(String)
}

@MainActor
@Observable
final class BriefingViewModel {
    enum TaskKey: Hashable {
        case lens(String)
        case readFlush
        case snapshotSave
    }

    struct ReadVersionCompatibility {
        let oldestCompatibleVersion: Int
        let currentVersion: Int
    }

    enum PagingError: LocalizedError {
        case missingCursor

        var errorDescription: String? {
            "The Briefing page was incomplete but did not include a continuation cursor."
        }
    }

    enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case empty
        case error(String)
    }

    typealias RefreshPhase = BriefingIndexSynchronizer.RefreshPhase

    private(set) var index: APIBriefingIndexResponse?
    private(set) var orderedLenses: [APIBriefingLensSummary] = []
    var lensStates: [String: BriefingLensState] = [:]
    private(set) var state: LoadState = .idle
    private(set) var refreshPhase: RefreshPhase = .idle
    private var destination: BriefingDestination?
    private(set) var isActive = false
    /// Direct loads may prefetch; lifecycle deactivation pauses speculative neighbors.
    private var allowsNeighborPrefetch = true
    /// True while the selected lens is scrolled into reading — the masthead
    /// above the pager collapses to hand the space to the content.
    private(set) var isMastheadCompact = false
    /// True while the news category strip is showing beneath the tier strip.
    /// Expanded at the top of a news page (or after tapping News while
    /// reading); collapses as soon as the reader scrolls down.
    private(set) var isCategoryStripExpanded = false

    let service: BriefingServicing
    let narrationController: BriefingNarrationController
    private let snapshotStore: BriefingSnapshotStoring?
    private let indexSynchronizer: BriefingIndexSynchronizer
    private let firstRunCoordinator: BriefingFirstRunCoordinator
    private let indexFreshnessInterval: TimeInterval
    private let initialIndexRetryDelays: [UInt64]
    private let now: () -> Date
    let lensRetention: BriefingLensRetentionPolicy
    let tasks = TaskBag<TaskKey>()
    private var pendingReadKeys: Set<String> = []
    var optimisticallyReadSourceKeys: Set<String> = []
    private var sourceByKey: [String: APIBriefingSource] = [:]
    private var lensKeysBySourceKey: [String: Set<String>] = [:]
    var readVersionCompatibility: ReadVersionCompatibility?
    private var headerPinnedLensKeys: Set<String> = []
    /// The news category to return to when the reader re-enters the news tier.
    private var lastNewsLensKey: String?
    /// Keeps the category strip open after an explicit News tap while the
    /// masthead is compact; cleared on the next scroll-down.
    private var categoryStripPinnedOpen = false
    private var dismissedFirstRunID: Int?
    private var lastValidatedAt: Date?

    init(
        service: BriefingServicing,
        audioEpisodeService: any BriefingAudioEpisodeServicing,
        playbackService: any BriefingNarrationPlaybackControlling,
        snapshotStore: BriefingSnapshotStoring? = nil,
        refreshPollDelays: [UInt64] = briefingRefreshPollDelaysNanoseconds,
        firstRunCompletionRetryDelay: UInt64 = briefingFirstRunCompletionRetryNanoseconds,
        lensRetentionScheduler: (any BriefingLensRetentionScheduling)? = nil,
        indexFreshnessInterval: TimeInterval = briefingIndexFreshnessInterval,
        initialIndexRetryDelays: [UInt64] = briefingInitialIndexRetryDelaysNanoseconds,
        now: @escaping () -> Date = { AppClock.now }
    ) {
        self.service = service
        self.snapshotStore = snapshotStore
        self.indexFreshnessInterval = indexFreshnessInterval
        self.initialIndexRetryDelays = initialIndexRetryDelays
        self.now = now
        self.narrationController = BriefingNarrationController(
            briefingService: service,
            audioEpisodeService: audioEpisodeService,
            playbackService: playbackService
        )
        self.lensRetention = BriefingLensRetentionPolicy(
            scheduler: lensRetentionScheduler ?? BriefingLensRetentionScheduler()
        )
        self.firstRunCoordinator = BriefingFirstRunCoordinator(
            service: service,
            completionRetryDelay: firstRunCompletionRetryDelay
        )
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
    }

    var selectedLens: APIBriefingLensResponse? {
        guard let selectedLensKey else { return nil }
        return lenses[selectedLensKey]
    }

    func noteFirstPassageVisible(for lensKey: String) {
        BriefingPerformance.signposter.emitEvent("first-passage-visible")
        briefingRefreshLogger.info(
            "First Lens passage visible | key=\(lensKey, privacy: .public)"
        )
    }

    var isRefreshing: Bool {
        refreshPhase == .requesting || refreshPhase == .waitingForVersion
    }

    var selectedLensKey: String? {
        guard case .lens(let key) = destination else { return nil }
        return key
    }

    var firstRun: APIBriefingFirstRunProgress? {
        guard let progress = index?.firstRun, progress.runId != dismissedFirstRunID else {
            return nil
        }
        return progress
    }

    var isStartHereSelected: Bool { destination == .startHere }

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

    /// Pages the content pager swipes through: news categories followed by the
    /// fixed podcast and article lenses, matching the top-level tier strip.
    var pagerLenses: [APIBriefingLensSummary] {
        newsLenses + fixedLenses
    }

    private var selectedLensSummary: APIBriefingLensSummary? {
        orderedLenses.first { $0.key == selectedLensKey }
    }

    func setActive(_ active: Bool) {
        guard isActive != active else { return }
        if !active, let selectedLensKey {
            beginLensRetention(for: selectedLensKey)
        }
        isActive = active
        allowsNeighborPrefetch = active
        if active, let selectedLensKey {
            protectLens(selectedLensKey)
        }
        indexSynchronizer.setActive(active) { [weak self] in
            await self?.loadIndexIfNeeded()
        }
        if !active {
            cancelBackgroundLensLoads()
            firstRunCoordinator.stopPolling()
        } else {
            scheduleFirstRunPollIfNeeded()
        }
    }

    func loadIndexIfNeeded() async {
        guard index == nil else {
            await refreshIndexIfStale()
            return
        }
        // Cold start: paint the last briefing immediately, then revalidate
        // against the server via ETag; a version bump refetches the lenses.
        if await restoreFromSnapshot() {
            await refreshIndexIfStale()
            return
        }
        await loadIndex(force: true, retryTransientFailures: true)
    }

    func refreshIndex() async {
        await loadIndex(force: false)
    }

    private func refreshIndexIfStale() async {
        if let lastValidatedAt {
            let age = now().timeIntervalSince(lastValidatedAt)
            if age >= 0, age < indexFreshnessInterval {
                briefingRefreshLogger.info(
                    "Index refresh skipped | reason=fresh age_seconds=\(Int(age), privacy: .public) freshness_seconds=\(Int(self.indexFreshnessInterval), privacy: .public)"
                )
                loadWorkingSet()
                return
            }
        }
        await refreshIndex()
    }

    func pullToRefresh() async {
        await indexSynchronizer.refresh(
            prepare: { [weak self] in
                self?.cancelBackgroundLensLoads()
                self?.tasks.cancel(.readFlush)
                await self?.flushPendingReadMarks()
            },
            onIndexResult: { [weak self] result in
                self?.applyValidatedIndexResult(result)
            }
        )
    }

    func selectLens(key: String) {
        guard destination != .lens(key) else { return }
        if firstRun != nil {
            dismissFirstRun()
        }
        if let selectedLensKey {
            beginLensRetention(for: selectedLensKey)
        }
        destination = .lens(key)
        protectLens(key)
        cancelLensLoads(except: key)
        noteSelectionChanged()
        loadWorkingSet()
    }

    func selectStartHere() {
        guard firstRun != nil, destination != .startHere else { return }
        if let selectedLensKey {
            beginLensRetention(for: selectedLensKey)
        }
        destination = .startHere
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
        if let selectedLensKey {
            materializeRenderModelIfNeeded(for: selectedLensKey)
        }
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

    func markAllSourcesRead(in lensKey: String) async throws {
        guard orderedLenses.contains(where: {
            $0.key == lensKey && $0.unreadSourceCount > 0
        }) else { return }

        tasks.cancel(.readFlush)
        await flushPendingReadMarks()
        let response = try await service.markLensRead(key: lensKey)

        let loadedUnreadKeys = lensStates[lensKey]?.document?.sources.compactMap { source in
            source.read ? nil : source.sourceKey
        } ?? []
        if !loadedUnreadKeys.isEmpty {
            markSourcesReadLocally(loadedUnreadKeys)
        }
        setUnreadSourceCount(0, for: lensKey)

        indexSynchronizer.cancelIndexLoad()
        do {
            if let result = try await indexSynchronizer.load(force: true) {
                applyValidatedIndexResult(result)
            }
        } catch {
            briefingRefreshLogger.error(
                "Category read reconciliation failed | lens_key=\(lensKey, privacy: .public) version=\(response.version, privacy: .public) error=\(error.localizedDescription, privacy: .private)"
            )
        }
    }

    func source(for sourceKey: String) -> APIBriefingSource? {
        sourceByKey[sourceKey]
    }

    private func loadIndex(
        force: Bool,
        retryTransientFailures: Bool = false
    ) async {
        if index == nil {
            state = .loading
        }
        var retryIndex = 0
        while true {
            do {
                if let result = try await indexSynchronizer.load(force: force) {
                    applyValidatedIndexResult(result)
                }
                return
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                let transportCode = briefingTransientTransportCode(error)
                if retryTransientFailures,
                   let transportCode,
                   retryIndex < initialIndexRetryDelays.count {
                    let baseDelay = initialIndexRetryDelays[retryIndex]
                    let jitter = UInt64.random(
                        in: 0...min(baseDelay / 5, 100_000_000)
                    )
                    let delay = baseDelay + jitter
                    retryIndex += 1
                    briefingRefreshLogger.info(
                        "Initial index load retry scheduled | attempt=\(retryIndex, privacy: .public) transport_code=\(transportCode.rawValue, privacy: .public) delay_ms=\(delay / 1_000_000, privacy: .public)"
                    )
                    do {
                        try await Task.sleep(nanoseconds: delay)
                    } catch {
                        return
                    }
                    continue
                }
                if orderedLenses.isEmpty {
                    let code = transportCode.map { String($0.rawValue) } ?? "none"
                    briefingRefreshLogger.error(
                        "Initial index load failed | transport_code=\(code, privacy: .public) error=\(error.localizedDescription, privacy: .private)"
                    )
                    state = .error(error.localizedDescription)
                }
                return
            }
        }
    }

    private func applyValidatedIndexResult(_ result: BriefingIndexFetchResult) {
        lastValidatedAt = now()
        applyIndexResult(result)
        scheduleSnapshotSave()
    }

    private func applyIndexResult(_ result: BriefingIndexFetchResult) {
        switch result {
        case .notModified:
            state = orderedLenses.isEmpty && firstRun == nil ? .empty : .loaded
            loadWorkingSet()
        case .value(let response, _):
            applyIndex(response)
        }
    }

    private func applyIndex(_ response: APIBriefingIndexResponse) {
        if response.firstRun == nil {
            dismissedFirstRunID = nil
        }
        let versionChanged = index.map { $0.version != response.version } ?? false
        if let compatibility = readVersionCompatibility,
           response.version != compatibility.currentVersion {
            readVersionCompatibility = nil
        }
        if versionChanged {
            markLensesStale(validLensKeys: Set(response.lenses.map(\.key)))
        } else {
            let validKeys = Set(response.lenses.map(\.key))
            lensStates = lensStates.filter { validKeys.contains($0.key) }
            rebuildSourceIndex()
        }
        index = response
        orderedLenses = firstRun == nil
            ? Self.sortedLenses(response.lenses)
            : response.lenses
        state = response.lenses.isEmpty && firstRun == nil ? .empty : .loaded
        if firstRun != nil {
            destination = .startHere
        } else if selectedLensKey == nil
                    || !response.lenses.contains(where: { $0.key == selectedLensKey }) {
            destination = orderedLenses.first.map { .lens($0.key) }
        }
        noteSelectionChanged()
        loadWorkingSet()
        scheduleSnapshotSave()
        scheduleFirstRunPollIfNeeded()
    }

    private func loadWorkingSet() {
        let workingSetKeys = workingSetLensKeys()
        let workingSet = Set(workingSetKeys)
        for key in Set(orderedLenses.map(\.key)).union(lensStates.keys)
            where !workingSet.contains(key) {
            tasks.cancel(.lens(key))
            mutateLensState(key) { $0.loadPhase = .idle }
        }
        guard let selectedKey = workingSetKeys.first else { return }
        loadLensIfNeeded(key: selectedKey)
        if let selected = lenses[selectedKey],
           lensStates[selectedKey]?.isStale != true,
           !selected.hasMore,
           selected.version == index?.version {
            prefetchNeighborsIfSelected(selectedKey)
        }
    }

    func prefetchNeighborsIfSelected(_ loadedKey: String) {
        guard allowsNeighborPrefetch,
              loadedKey == selectedLensKey,
              let selected = lenses[loadedKey],
              !selected.hasMore,
              lensStates[loadedKey]?.isStale != true else {
            return
        }
        for key in workingSetLensKeys().dropFirst() {
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
            mutateLensState(key) { $0.loadPhase = .idle }
        }
    }

    private func cancelLensLoads(except retainedKey: String) {
        for key in Set(orderedLenses.map(\.key)).union(lensStates.keys) where key != retainedKey {
            tasks.cancel(.lens(key))
            mutateLensState(key) { $0.loadPhase = .idle }
        }
    }

    private func cancelLensLoads() {
        for key in Set(orderedLenses.map(\.key)).union(lensStates.keys) {
            tasks.cancel(.lens(key))
            mutateLensState(key) { $0.loadPhase = .idle }
        }
    }

    /// Applies the persisted briefing so the tab renders without waiting on
    /// the network. Returns false when there is nothing usable to restore.
    private func restoreFromSnapshot() async -> Bool {
        guard let snapshot = await snapshotStore?.load(),
              !snapshot.index.lenses.isEmpty || snapshot.index.firstRun != nil
        else { return false }
        index = snapshot.index
        orderedLenses = firstRun == nil
            ? Self.sortedLenses(snapshot.index.lenses)
            : snapshot.index.lenses
        lensStates = snapshot.lenses.mapValues { BriefingLensState(document: $0) }
        rebuildSourceIndex()
        indexSynchronizer.restore(etag: snapshot.etag)
        lastValidatedAt = snapshot.lastValidatedAt
        state = .loaded
        if firstRun != nil {
            destination = .startHere
        } else if let savedKey = snapshot.selectedLensKey,
           snapshot.index.lenses.contains(where: { $0.key == savedKey }) {
            destination = .lens(savedKey)
        } else if selectedLensKey == nil
                    || !snapshot.index.lenses.contains(where: { $0.key == selectedLensKey }) {
            destination = orderedLenses.first.map { .lens($0.key) }
        }
        noteSelectionChanged()
        scheduleFirstRunPollIfNeeded()
        return true
    }

    func scheduleSnapshotSave() {
        guard let snapshotStore else { return }
        tasks.runReplacing(.snapshotSave) { [weak self] in
            do {
                try await Task.sleep(nanoseconds: 500_000_000)
            } catch {
                return
            }
            guard let self, !Task.isCancelled, let index = self.index else { return }
            let workingSet = Set(self.workingSetLensKeys())
            let snapshotLenses = self.lensStates.reduce(
                into: [String: APIBriefingLensResponse]()
            ) { result, entry in
                guard workingSet.contains(entry.key),
                      !entry.value.isStale,
                      let document = entry.value.document else { return }
                result[entry.key] = document
            }
            await snapshotStore.save(
                BriefingSnapshot(
                    userID: snapshotStore.userID,
                    index: self.indexForSnapshot(index),
                    etag: self.indexSynchronizer.etag,
                    selectedLensKey: self.selectedLensKey,
                    lenses: snapshotLenses,
                    lastValidatedAt: self.lastValidatedAt,
                    savedAt: AppClock.now
                )
            )
        }
    }

    private func indexForSnapshot(
        _ currentIndex: APIBriefingIndexResponse
    ) -> APIBriefingIndexResponse {
        guard currentIndex.firstRun?.runId == dismissedFirstRunID else {
            return currentIndex
        }
        return APIBriefingIndexResponse(
            version: currentIndex.version,
            mastheadTitle: currentIndex.mastheadTitle,
            mastheadDeck: currentIndex.mastheadDeck,
            generatedAt: currentIndex.generatedAt,
            lenses: currentIndex.lenses,
            firstRun: nil
        )
    }

    private func markLensesStale(validLensKeys: Set<String>) {
        let oldLensKeys = Set(index?.lenses.map(\.key) ?? [])
        let knownLensKeys = validLensKeys
            .union(oldLensKeys)
            .union(lensStates.keys)
        for key in knownLensKeys where !validLensKeys.contains(key) {
            tasks.cancel(.lens(key))
            lensRetention.protect(key)
        }
        lensStates = lensStates.filter { validLensKeys.contains($0.key) }
        for key in validLensKeys {
            invalidateLensForIndexChange(key)
        }
        rebuildSourceIndex()
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
            let acceptedVersion = index?.version
            let response = try await service.markRead(sourceKeys: keys)
            indexSynchronizer.cancelIndexLoad()
            if let acceptedVersion,
               response.retired == 0,
               response.version == acceptedVersion + 1 {
                readVersionCompatibility = ReadVersionCompatibility(
                    oldestCompatibleVersion: min(
                        readVersionCompatibility?.oldestCompatibleVersion ?? acceptedVersion,
                        acceptedVersion
                    ),
                    currentVersion: response.version
                )
                fastForwardLoadedBriefing(to: response.version)
                scheduleSnapshotSave()
                return
            }

            readVersionCompatibility = nil
            markCachedLensesStale(containing: keys)
            if var currentIndex = index {
                currentIndex = APIBriefingIndexResponse(
                    version: response.version,
                    mastheadTitle: currentIndex.mastheadTitle,
                    mastheadDeck: currentIndex.mastheadDeck,
                    generatedAt: currentIndex.generatedAt,
                    lenses: currentIndex.lenses,
                    firstRun: currentIndex.firstRun
                )
                index = currentIndex
                orderedLenses = firstRun == nil
                    ? Self.sortedLenses(currentIndex.lenses)
                    : currentIndex.lenses
            }
            scheduleSnapshotSave()
            await refreshIndex()
        } catch {
            pendingReadKeys.formUnion(keys)
            scheduleReadFlush(delayNanoseconds: briefingReadFlushRetryNanoseconds)
        }
    }

    private func fastForwardLoadedBriefing(to version: Int) {
        if let currentIndex = index {
            let updatedIndex = APIBriefingIndexResponse(
                version: version,
                mastheadTitle: currentIndex.mastheadTitle,
                mastheadDeck: currentIndex.mastheadDeck,
                generatedAt: currentIndex.generatedAt,
                lenses: currentIndex.lenses,
                firstRun: currentIndex.firstRun
            )
            index = updatedIndex
            orderedLenses = firstRun == nil
                ? Self.sortedLenses(updatedIndex.lenses)
                : updatedIndex.lenses
        }
        lensStates = lensStates.mapValues { state in
            guard let lens = state.document else { return state }
            var updated = state
            updated.document = APIBriefingLensResponse(
                    version: version,
                    lens: lens.lens,
                    segments: lens.segments,
                    sources: lens.sources,
                    nextCursor: lens.nextCursor,
                    hasMore: lens.hasMore
                )
            return updated
        }
    }

    private func markCachedLensesStale(containing sourceKeys: [String]) {
        let keySet = Set(sourceKeys)
        guard !keySet.isEmpty else { return }
        let affectedLensKeys = keySet.reduce(into: Set<String>()) { result, sourceKey in
            result.formUnion(lensKeysBySourceKey[sourceKey] ?? [])
        }
        for lensKey in affectedLensKeys {
            invalidateLens(lensKey, staleness: .readRetirement)
        }
    }

    /// Optimistic seen-time update: flip sources to read and rederive lens-chip
    /// unread counts. Read segments stay visible (greyed by the view); the server
    /// retires them and the next index fetch reconciles.
    private func markSourcesReadLocally(_ keys: [String]) {
        let signpostState = BriefingPerformance.signposter.beginInterval("optimistic-read-update")
        defer { BriefingPerformance.signposter.endInterval("optimistic-read-update", signpostState) }
        let startedAt = Date()
        let keySet = Set(keys)
        optimisticallyReadSourceKeys.formUnion(keySet)
        for key in keySet {
            if let source = sourceByKey[key] {
                sourceByKey[key] = applyingOptimisticReadOverlay(source)
            }
        }
        let affectedLensKeys = keySet.reduce(into: Set<String>()) { result, sourceKey in
            result.formUnion(lensKeysBySourceKey[sourceKey] ?? [])
        }
        var updatedSummaries: [String: APIBriefingLensSummary] = [:]
        for lensKey in affectedLensKeys {
            guard var state = lensStates[lensKey], let lens = state.document else { continue }
            let newlyReadCount = lens.sources.count(where: { source in
                keySet.contains(source.sourceKey) && !source.read
            })
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
            let updatedSummary = APIBriefingLensSummary(
                key: lens.lens.key,
                tier: lens.lens.tier,
                title: lens.lens.title,
                deck: lens.lens.deck,
                position: lens.lens.position,
                segmentCount: lens.lens.segmentCount,
                unreadSourceCount: max(lens.lens.unreadSourceCount - newlyReadCount, 0)
            )
            let updatedLens = APIBriefingLensResponse(
                version: lens.version,
                lens: updatedSummary,
                segments: lens.segments,
                sources: updatedSources,
                nextCursor: lens.nextCursor,
                hasMore: lens.hasMore
            )
            state.document = updatedLens
            if lensKey == selectedLensKey {
                state.renderModel = makeRenderModel(
                    updatedLens,
                    for: lensKey,
                    reusing: state.renderModel,
                    affectedSourceKeys: keySet
                )
            } else {
                state.renderModel = nil
            }
            lensStates[lensKey] = state
            updatedSummaries[lensKey] = updatedSummary
        }
        if var currentIndex = index {
            currentIndex = APIBriefingIndexResponse(
                version: currentIndex.version,
                mastheadTitle: currentIndex.mastheadTitle,
                mastheadDeck: currentIndex.mastheadDeck,
                generatedAt: currentIndex.generatedAt,
                lenses: currentIndex.lenses.map { updatedSummaries[$0.key] ?? $0 },
                firstRun: currentIndex.firstRun
            )
            index = currentIndex
            orderedLenses = firstRun == nil
                ? Self.sortedLenses(currentIndex.lenses)
                : currentIndex.lenses
        }
        briefingRefreshLogger.info(
            "Optimistic read state published | source_count=\(keySet.count, privacy: .public) affected_lenses=\(affectedLensKeys.count, privacy: .public) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public)"
        )
        scheduleSnapshotSave()
    }

    private func setUnreadSourceCount(_ count: Int, for lensKey: String) {
        guard let currentIndex = index,
              currentIndex.lenses.contains(where: {
                  $0.key == lensKey && $0.unreadSourceCount != count
              }) else { return }
        let updatedIndex = APIBriefingIndexResponse(
            version: currentIndex.version,
            mastheadTitle: currentIndex.mastheadTitle,
            mastheadDeck: currentIndex.mastheadDeck,
            generatedAt: currentIndex.generatedAt,
            lenses: currentIndex.lenses.map { lens in
                guard lens.key == lensKey else { return lens }
                return APIBriefingLensSummary(
                    key: lens.key,
                    tier: lens.tier,
                    title: lens.title,
                    deck: lens.deck,
                    position: lens.position,
                    segmentCount: lens.segmentCount,
                    unreadSourceCount: count
                )
            },
            firstRun: currentIndex.firstRun
        )
        index = updatedIndex
        orderedLenses = firstRun == nil
            ? Self.sortedLenses(updatedIndex.lenses)
            : updatedIndex.lenses
        scheduleSnapshotSave()
    }

    func reindexSources(
        for lensKey: String,
        replacing previousLens: APIBriefingLensResponse?,
        with lens: APIBriefingLensResponse?
    ) {
        for sourceKey in previousLens?.sources.map(\.sourceKey) ?? [] {
            lensKeysBySourceKey[sourceKey]?.remove(lensKey)
            if lensKeysBySourceKey[sourceKey]?.isEmpty == true {
                lensKeysBySourceKey.removeValue(forKey: sourceKey)
                sourceByKey.removeValue(forKey: sourceKey)
            } else if let replacementLensKey = lensKeysBySourceKey[sourceKey]?.first,
                      let replacement = lensStates[replacementLensKey]?.document?.sources.first(where: {
                          $0.sourceKey == sourceKey
                      }) {
                sourceByKey[sourceKey] = replacement
            }
        }
        for source in lens?.sources ?? [] {
            lensKeysBySourceKey[source.sourceKey, default: []].insert(lensKey)
            sourceByKey[source.sourceKey] = source
        }
    }

    private func rebuildSourceIndex() {
        sourceByKey.removeAll(keepingCapacity: true)
        lensKeysBySourceKey.removeAll(keepingCapacity: true)
        for (lensKey, state) in lensStates {
            guard let lens = state.document else { continue }
            for source in lens.sources {
                lensKeysBySourceKey[source.sourceKey, default: []].insert(lensKey)
                sourceByKey[source.sourceKey] = source
            }
        }
    }

    private static func sortedLenses(_ lenses: [APIBriefingLensSummary]) -> [APIBriefingLensSummary] {
        lenses.sorted { left, right in
            if left.position == right.position {
                return left.key < right.key
            }
            return left.position < right.position
        }
    }

    private func scheduleFirstRunPollIfNeeded() {
        firstRunCoordinator.setPolling(firstRun != nil) { [weak self] in
            await self?.refreshIndex()
        }
    }

    private func dismissFirstRun() {
        guard let progress = firstRun, let currentIndex = index else { return }
        dismissedFirstRunID = progress.runId
        orderedLenses = Self.sortedLenses(currentIndex.lenses)
        firstRunCoordinator.stopPolling()
        scheduleSnapshotSave()
        firstRunCoordinator.complete()
    }
}

private func briefingTransientTransportCode(_ error: Error) -> URLError.Code? {
    if let urlError = error as? URLError {
        return briefingRetryableTransportCodes.contains(urlError.code) ? urlError.code : nil
    }
    if case APIError.networkError(let underlyingError) = error {
        return briefingTransientTransportCode(underlyingError)
    }
    let nsError = error as NSError
    guard nsError.domain == NSURLErrorDomain else { return nil }
    let code = URLError.Code(rawValue: nsError.code)
    return briefingRetryableTransportCodes.contains(code) ? code : nil
}

private let briefingRetryableTransportCodes: Set<URLError.Code> = [
    .timedOut,
    .cannotFindHost,
    .cannotConnectToHost,
    .networkConnectionLost,
    .dnsLookupFailed,
    .notConnectedToInternet,
    .resourceUnavailable,
]
