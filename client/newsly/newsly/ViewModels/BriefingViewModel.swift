import Foundation
import SwiftUI

@MainActor
final class BriefingViewModel: ObservableObject {
    enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case empty
        case error(String)
    }

    @Published private(set) var index: APIBriefingIndexResponse?
    @Published private(set) var lenses: [String: APIBriefingLensResponse] = [:]
    @Published private(set) var state: LoadState = .idle
    @Published var selectedLensKey: String?
    @Published private(set) var narrationEpisodes: [String: AudioEpisode] = [:]

    private let service: BriefingServicing
    private var etag: String?
    private var indexLoadTask: Task<Void, Never>?
    private var loadedLensKeys: Set<String> = []
    private var lensLoadTasks: [String: Task<Void, Never>] = [:]
    private var pendingReadKeys: Set<String> = []
    private var readFlushTask: Task<Void, Never>?
    private var headerPinnedLensKeys: Set<String> = []

    /// True when the lens we just navigated away from had its category strip
    /// pinned — the incoming page should start with its strip pinned too.
    private(set) var carryHeaderPinned = false

    init(service: BriefingServicing) {
        self.service = service
    }

    var orderedLenses: [APIBriefingLensSummary] {
        (index?.lenses ?? []).sorted { left, right in
            if left.position == right.position {
                return left.key < right.key
            }
            return left.position < right.position
        }
    }

    var selectedLens: APIBriefingLensResponse? {
        guard let selectedLensKey else { return nil }
        return lenses[selectedLensKey]
    }

    func handleTabEntered() {
        Task { await loadIndexIfNeeded() }
    }

    func loadIndexIfNeeded() async {
        guard index == nil else {
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
        carryHeaderPinned = selectedLensKey.map(headerPinnedLensKeys.contains) ?? false
        selectedLensKey = key
        loadLensIfNeeded(key: key)
        prefetchNeighbors(around: key)
    }

    func setHeaderPinned(_ pinned: Bool, forLens key: String) {
        if pinned {
            headerPinnedLensKeys.insert(key)
        } else {
            headerPinnedLensKeys.remove(key)
        }
    }

    func headerPinned(forLens key: String) -> Bool {
        headerPinnedLensKeys.contains(key)
    }

    func loadLensIfNeeded(key: String) {
        guard !loadedLensKeys.contains(key), lensLoadTasks[key] == nil else { return }
        lensLoadTasks[key] = Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await service.fetchLens(key: key)
                self.lenses[key] = response
                self.loadedLensKeys.insert(key)
            } catch {
                if !isNetworkCancellation(error) {
                    self.state = .error(error.localizedDescription)
                }
            }
            self.lensLoadTasks[key] = nil
        }
    }

    func markSegmentSeen(_ segment: APIBriefingSegment) {
        let unreadKeys = segment.sourceKeys.filter { source(for: $0)?.read == false }
        guard !unreadKeys.isEmpty else { return }
        pendingReadKeys.formUnion(unreadKeys)
        readFlushTask?.cancel()
        readFlushTask = Task { [weak self] in
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
                guard shouldApply(response) else {
                    state = orderedLenses.isEmpty ? .empty : .loaded
                    return
                }
                index = response
                // A missing ETag header must not discard the last known validator.
                etag = responseEtag ?? etag
                state = response.lenses.isEmpty ? .empty : .loaded
                if selectedLensKey == nil || !response.lenses.contains(where: { $0.key == selectedLensKey }) {
                    selectedLensKey = orderedLenses.first?.key
                }
                if let selectedLensKey {
                    loadLensIfNeeded(key: selectedLensKey)
                    prefetchNeighbors(around: selectedLensKey)
                }
            }
        } catch {
            if !isNetworkCancellation(error) {
                state = .error(error.localizedDescription)
            }
        }
    }

    // A read-mark can bump the version while an index fetch is in flight; never
    // let the older snapshot overwrite the newer state.
    private func shouldApply(_ response: APIBriefingIndexResponse) -> Bool {
        guard let current = index else { return true }
        return response.version >= current.version
    }

    private func prefetchNeighbors(around key: String) {
        let lenses = orderedLenses
        guard let index = lenses.firstIndex(where: { $0.key == key }) else { return }
        for neighborIndex in [index - 1, index + 1] where lenses.indices.contains(neighborIndex) {
            loadLensIfNeeded(key: lenses[neighborIndex].key)
        }
    }

    private func flushPendingReadMarks() async {
        let keys = Array(pendingReadKeys).sorted()
        pendingReadKeys.removeAll()
        guard !keys.isEmpty else { return }
        do {
            let response = try await service.markRead(sourceKeys: keys)
            applyReadMarksLocally(keys, version: response.version)
        } catch {
            pendingReadKeys.formUnion(keys)
        }
    }

    private func applyReadMarksLocally(_ keys: [String], version: Int) {
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
                    read: true
                )
            }
            let readSourceKeys = Set(updatedSources.filter(\.read).map(\.sourceKey))
            let remainingSegments = lens.segments.filter { segment in
                let segmentSourceKeys = Set(segment.sourceKeys)
                return segmentSourceKeys.isEmpty || !segmentSourceKeys.isSubset(of: readSourceKeys)
            }
            let remainingSourceKeys = Set(remainingSegments.flatMap(\.sourceKeys))
            let updatedSummary = APIBriefingLensSummary(
                key: lens.lens.key,
                tier: lens.lens.tier,
                title: lens.lens.title,
                deck: lens.lens.deck,
                position: lens.lens.position,
                segmentCount: remainingSegments.count,
                unreadSourceCount: remainingSourceKeys.subtracting(readSourceKeys).count
            )
            lenses[lensKey] = APIBriefingLensResponse(
                version: version,
                lens: updatedSummary,
                segments: remainingSegments,
                sources: updatedSources
            )
            updatedSummaries[lensKey] = updatedSummary
        }
        if var currentIndex = index {
            currentIndex = APIBriefingIndexResponse(
                version: version,
                mastheadTitle: currentIndex.mastheadTitle,
                mastheadDeck: currentIndex.mastheadDeck,
                generatedAt: currentIndex.generatedAt,
                lenses: currentIndex.lenses.map { updatedSummaries[$0.key] ?? $0 }
            )
            index = currentIndex
        }
    }
}
