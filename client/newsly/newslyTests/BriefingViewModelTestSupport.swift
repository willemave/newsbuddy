import Foundation
import XCTest
@testable import newsly

@MainActor
final class MockBriefingService: BriefingServicing {
    struct LensFetch: Equatable {
        let key: String
        let limit: Int?
        let cursor: String?
    }

    var indexResults: [BriefingIndexFetchResult] = []
    var indexError: Error?
    var indexErrors: [Error?] = []
    var indexEtags: [String?] = []
    var fetchIndexDelayNanoseconds: UInt64?
    var fetchIndexWaitRequestIndices: Set<Int> = []
    var fetchIndexErrorsByRequestIndex: [Int: Error] = [:]
    var lensResponses: [String: APIBriefingLensResponse] = [:]
    var lensPageResponses: [String: [APIBriefingLensResponse]] = [:]
    var fetchLensDelayNanoseconds: UInt64?
    var fetchLensDelaysNanoseconds: [UInt64] = []
    var fetchLensErrors: [Error?] = []
    var fetchLensWaitRequestIndices: Set<Int> = []
    var fetchLensErrorsByRequestIndex: [Int: Error] = [:]
    var fetchLensKeys: [String] = []
    var fetchLensRequests: [LensFetch] = []
    var markReadCalls: [[String]] = []
    var markLensReadKeys: [String] = []
    var events: [String] = []
    var readMarkResponse = APIBriefingReadMarkResponse(marked: 0, retired: 0, version: 1)
    var readMarkResponses: [APIBriefingReadMarkResponse] = []
    var lensReadMarkResponse = APIBriefingReadMarkResponse(marked: 0, retired: 0, version: 1)
    var markReadDelaysNanoseconds: [UInt64] = []
    var markReadWaitsForResume = false
    var markReadError: Error?
    var markLensReadError: Error?
    var refreshError: Error?
    var refreshDelayNanoseconds: UInt64?
    var refreshWaitsForResume = false
    var digSearchFragments: [String] = []
    var digSearchErrors: [Error?] = []
    var digSummarizePassageContexts: [String] = []
    var digSummaries: [String] = []
    private(set) var refreshRequestCount = 0
    var narrationManifest: BriefingNarration?
    var narrationManifests: [BriefingNarration] = []
    var narrationManifestsByLens: [String: BriefingNarration] = [:]
    var narrationFetchResults: [Result<BriefingNarration, Error>] = []
    var narrationError: Error?
    var narrationRequestDelayNanoseconds: UInt64?
    var narrationWaitsForCancellation = false
    var narrationRequestWaitLensKeys: Set<String> = []
    var narrationLensKeys: [String] = []
    var narrationFetchEpisodeGroupIDs: [String] = []
    private(set) var narrationCancellationCount = 0
    private(set) var markReadCancellationCount = 0
    var firstRunCompletionFailuresRemaining = 0
    private(set) var firstRunCompletionCount = 0

    private var refreshContinuation: CheckedContinuation<Void, Never>?
    private var markReadContinuation: CheckedContinuation<Void, Never>?
    private var fetchIndexContinuations: [Int: CheckedContinuation<Void, Never>] = [:]
    private var fetchLensContinuations: [Int: CheckedContinuation<Void, Never>] = [:]
    private var latestNarration: BriefingNarration?
    private var narrationRequestContinuations: [String: CheckedContinuation<Void, Never>] = [:]

    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult {
        let requestIndex = indexEtags.count
        indexEtags.append(etag)
        events.append("fetchIndex:\(etag ?? "nil")")
        if fetchIndexWaitRequestIndices.contains(requestIndex) {
            await withCheckedContinuation { continuation in
                fetchIndexContinuations[requestIndex] = continuation
            }
        }
        if let fetchIndexDelayNanoseconds {
            try? await Task.sleep(nanoseconds: fetchIndexDelayNanoseconds)
        }
        if let requestError = fetchIndexErrorsByRequestIndex.removeValue(forKey: requestIndex) {
            throw requestError
        }
        let queuedError = indexErrors.isEmpty ? nil : indexErrors.removeFirst()
        if let queuedError {
            throw queuedError
        }
        if let indexError {
            throw indexError
        }
        guard !indexResults.isEmpty else {
            return .value(makeIndex(lenses: []), etag: nil)
        }
        return indexResults.removeFirst()
    }

    func fetchLens(
        key: String,
        limit: Int?,
        cursor: String?
    ) async throws -> APIBriefingLensResponse {
        let requestIndex = fetchLensRequests.count
        fetchLensKeys.append(key)
        fetchLensRequests.append(LensFetch(key: key, limit: limit, cursor: cursor))
        events.append("fetchLens:\(key)")
        if fetchLensWaitRequestIndices.contains(requestIndex) {
            await withCheckedContinuation { continuation in
                fetchLensContinuations[requestIndex] = continuation
            }
        }
        let delay = fetchLensDelaysNanoseconds.isEmpty
            ? fetchLensDelayNanoseconds
            : fetchLensDelaysNanoseconds.removeFirst()
        if let delay, delay > 0 {
            try? await Task.sleep(nanoseconds: delay)
        }
        if let requestError = fetchLensErrorsByRequestIndex.removeValue(forKey: requestIndex) {
            throw requestError
        }
        let error = fetchLensErrors.isEmpty ? nil : fetchLensErrors.removeFirst()
        if let error {
            throw error
        }
        let response: APIBriefingLensResponse
        if var pages = lensPageResponses[key], !pages.isEmpty {
            response = pages.removeFirst()
            lensPageResponses[key] = pages
        } else {
            response = lensResponses[key] ?? makeLens(key: key)
        }
        return response
    }

    func markRead(sourceKeys: [String]) async throws -> APIBriefingReadMarkResponse {
        markReadCalls.append(sourceKeys)
        events.append("markRead:\(sourceKeys.joined(separator: ","))")
        if !markReadDelaysNanoseconds.isEmpty {
            let delay = markReadDelaysNanoseconds.removeFirst()
            do {
                try await Task.sleep(nanoseconds: delay)
            } catch {
                markReadCancellationCount += 1
                throw error
            }
        }
        if markReadWaitsForResume {
            await withCheckedContinuation { continuation in
                markReadContinuation = continuation
            }
        }
        if let markReadError {
            throw markReadError
        }
        if !readMarkResponses.isEmpty {
            return readMarkResponses.removeFirst()
        }
        return readMarkResponse
    }

    func markLensRead(key: String) async throws -> APIBriefingReadMarkResponse {
        markLensReadKeys.append(key)
        events.append("markLensRead:\(key)")
        if let markLensReadError {
            throw markLensReadError
        }
        return lensReadMarkResponse
    }

    func requestRefresh() async throws -> APIBriefingRefreshResponse {
        refreshRequestCount += 1
        events.append("requestRefresh")
        if refreshWaitsForResume {
            await withCheckedContinuation { continuation in
                refreshContinuation = continuation
            }
        }
        if let refreshDelayNanoseconds {
            try await Task.sleep(nanoseconds: refreshDelayNanoseconds)
        }
        if let refreshError {
            throw refreshError
        }
        return APIBriefingRefreshResponse(enqueued: true, version: 1)
    }

    func completeFirstRun() async throws {
        firstRunCompletionCount += 1
        if firstRunCompletionFailuresRemaining > 0 {
            firstRunCompletionFailuresRemaining -= 1
            throw NSError(domain: "MockBriefingService", code: 1)
        }
    }

    func resumeRefreshRequest() {
        refreshContinuation?.resume()
        refreshContinuation = nil
    }

    func resumeIndexRequest(at requestIndex: Int) {
        fetchIndexContinuations.removeValue(forKey: requestIndex)?.resume()
    }

    func resumeLensRequest(at requestIndex: Int) {
        fetchLensContinuations.removeValue(forKey: requestIndex)?.resume()
    }

    func resumeMarkRead() {
        markReadContinuation?.resume()
        markReadContinuation = nil
    }

    func digSearch(fragment: String) async throws -> APIBriefingDigSearchResponse {
        digSearchFragments.append(fragment)
        let error = digSearchErrors.isEmpty ? nil : digSearchErrors.removeFirst()
        if let error {
            throw error
        }
        return APIBriefingDigSearchResponse(results: [], elapsedMs: 0)
    }

    func digSummarize(
        fragment: String,
        passageContext: String,
        results: [APIBriefingDigSearchResult]
    ) async throws -> APIBriefingDigSummarizeResponse {
        digSummarizePassageContexts.append(passageContext)
        let summary = digSummaries.isEmpty ? "Summary" : digSummaries.removeFirst()
        return APIBriefingDigSummarizeResponse(summary: summary, model: "test", elapsedMs: 0)
    }

    func requestNarration(lensKey: String) async throws -> BriefingNarration {
        narrationLensKeys.append(lensKey)
        if narrationRequestWaitLensKeys.contains(lensKey) {
            await withCheckedContinuation { continuation in
                narrationRequestContinuations[lensKey] = continuation
            }
        }
        if let narrationRequestDelayNanoseconds {
            try await Task.sleep(nanoseconds: narrationRequestDelayNanoseconds)
        }
        if narrationWaitsForCancellation {
            do {
                try await Task.sleep(nanoseconds: 60_000_000_000)
            } catch {
                narrationCancellationCount += 1
                throw error
            }
        }
        if let narrationError {
            throw narrationError
        }
        let narration: BriefingNarration
        if let lensNarration = narrationManifestsByLens[lensKey] {
            narration = lensNarration
        } else if !narrationManifests.isEmpty {
            narration = narrationManifests.removeFirst()
        } else if let narrationManifest {
            narration = narrationManifest
        } else {
            throw NSError(domain: "MockBriefingService", code: 2)
        }
        latestNarration = narration
        return narration
    }

    func resumeNarrationRequest(lensKey: String) {
        narrationRequestContinuations.removeValue(forKey: lensKey)?.resume()
    }

    func fetchNarration(episodeGroupID: String) async throws -> BriefingNarration {
        narrationFetchEpisodeGroupIDs.append(episodeGroupID)
        if !narrationFetchResults.isEmpty {
            let narration = try narrationFetchResults.removeFirst().get()
            guard narration.episodeGroupId == episodeGroupID else {
                throw NSError(domain: "MockBriefingService", code: 3)
            }
            latestNarration = narration
            return narration
        }
        guard let latestNarration,
              latestNarration.episodeGroupId == episodeGroupID else {
            throw NSError(domain: "MockBriefingService", code: 3)
        }
        return latestNarration
    }
}

@MainActor
final class MockBriefingAudioEpisodeService: BriefingAudioEpisodeServicing {
    private(set) var streamedEpisodeIDs: [Int] = []

    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        streamedEpisodeIDs.append(episode.id)
        return AuthorizedMediaResource(
            url: URL(string: "https://example.test/audio/\(episode.id)")!,
            headers: [:]
        )
    }
}

@MainActor
final class MockBriefingNarrationPlaybackService: BriefingNarrationPlaybackControlling {
    private(set) var isSpeaking = false
    private(set) var playbackRate: Float = 1
    private(set) var speakingTarget: NarrationTarget?
    private(set) var playedTargets: [NarrationTarget] = []
    private var finishedHandler: NarrationPlaybackFinishedHandler?

    func pause() {
        isSpeaking = false
    }

    func stop() {
        isSpeaking = false
        speakingTarget = nil
        finishedHandler = nil
    }

    func playStreamingNarration(
        for target: NarrationTarget,
        rate: Float,
        onFinished: NarrationPlaybackFinishedHandler?,
        fetchStreamResource: () async throws -> AuthorizedMediaResource
    ) async throws {
        _ = try await fetchStreamResource()
        playbackRate = rate
        speakingTarget = target
        isSpeaking = true
        playedTargets.append(target)
        finishedHandler = onFinished
    }

    func finishCurrent() {
        guard let target = speakingTarget else { return }
        let handler = finishedHandler
        isSpeaking = false
        speakingTarget = nil
        finishedHandler = nil
        handler?(target)
    }
}

@MainActor
final class MockBriefingLensRetentionScheduler: BriefingLensRetentionScheduling {
    private var expiryActions: [String: @MainActor () -> Void] = [:]

    func scheduleExpiry(
        for lensKey: String,
        action: @escaping @MainActor () -> Void
    ) {
        expiryActions[lensKey] = action
    }

    func cancelExpiry(for lensKey: String) {
        expiryActions.removeValue(forKey: lensKey)
    }

    func expire(_ lensKey: String) {
        let action = expiryActions.removeValue(forKey: lensKey)
        action?()
    }
}

extension BriefingViewModel {
    convenience init(
        service: BriefingServicing,
        snapshotStore: BriefingSnapshotStoring? = nil,
        refreshPollDelays: [UInt64] = [1_000_000, 2_000_000, 5_000_000],
        firstRunCompletionRetryDelay: UInt64 = 1_000_000,
        lensRetentionScheduler: (any BriefingLensRetentionScheduling)? = nil,
        indexFreshnessInterval: TimeInterval = 15 * 60,
        initialIndexRetryDelays: [UInt64] = [],
        now: @escaping () -> Date = { AppClock.now }
    ) {
        self.init(
            service: service,
            audioEpisodeService: MockBriefingAudioEpisodeService(),
            playbackService: MockBriefingNarrationPlaybackService(),
            snapshotStore: snapshotStore,
            refreshPollDelays: refreshPollDelays,
            firstRunCompletionRetryDelay: firstRunCompletionRetryDelay,
            lensRetentionScheduler: lensRetentionScheduler,
            indexFreshnessInterval: indexFreshnessInterval,
            initialIndexRetryDelays: initialIndexRetryDelays,
            now: now
        )
    }
}

final class MockBriefingSnapshotStore: BriefingSnapshotStoring {
    let userID: Int
    var snapshotToLoad: BriefingSnapshot?
    private(set) var savedSnapshots: [BriefingSnapshot] = []
    private(set) var clearCalls = 0

    init(userID: Int) {
        self.userID = userID
    }

    func load() async -> BriefingSnapshot? {
        guard snapshotToLoad?.userID == userID else { return nil }
        return snapshotToLoad
    }

    func save(_ snapshot: BriefingSnapshot) async {
        guard snapshot.userID == userID else { return }
        savedSnapshots.append(snapshot)
    }

    func clear() async {
        clearCalls += 1
        snapshotToLoad = nil
    }
}

@MainActor
func waitForBriefingCondition(
    timeoutNanoseconds: UInt64 = 500_000_000,
    condition: @escaping @MainActor () -> Bool,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    let startedAt = DispatchTime.now().uptimeNanoseconds
    while !condition() {
        if DispatchTime.now().uptimeNanoseconds - startedAt > timeoutNanoseconds {
            XCTFail("Condition was not met before timeout", file: file, line: line)
            return
        }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

func makeIndex(
    version: Int = 1,
    lenses: [APIBriefingLensSummary],
    firstRun: APIBriefingFirstRunProgress? = nil
) -> APIBriefingIndexResponse {
    APIBriefingIndexResponse(
        version: version,
        mastheadTitle: "Today",
        mastheadDeck: "What matters now",
        generatedAt: Date(timeIntervalSince1970: 1_800_000_000),
        lenses: lenses,
        firstRun: firstRun
    )
}

func makeFirstRun(
    runID: Int = 1,
    revision: Int = 1,
    phase: APIBriefingFirstRunPhase = .active,
    connectedSourceCount: Int = 3,
    completedSources: [APIBriefingFirstRunSourceProgress] = [],
    activeSources: [String] = ["Techmeme", "Stratechery"],
    queuedSources: [String] = ["The Verge"],
    readyCategoryKeys: [String] = []
) -> APIBriefingFirstRunProgress {
    APIBriefingFirstRunProgress(
        runId: runID,
        revision: revision,
        phase: phase,
        connectedSourceCount: connectedSourceCount,
        completedSources: completedSources,
        activeSources: activeSources,
        queuedSources: queuedSources,
        readyCategoryKeys: readyCategoryKeys
    )
}

func makeLensSummary(
    key: String,
    title: String = "Today",
    position: Int = 0,
    tier: APIBriefingTier = .news,
    segmentCount: Int = 1,
    unreadSourceCount: Int = 2
) -> APIBriefingLensSummary {
    APIBriefingLensSummary(
        key: key,
        tier: tier,
        title: title,
        deck: "Latest unread reporting",
        position: position,
        segmentCount: segmentCount,
        unreadSourceCount: unreadSourceCount
    )
}

func makeLens(
    key: String,
    version: Int = 1,
    position: Int = 0,
    tier: APIBriefingTier = .news,
    segments: [APIBriefingSegment] = [makeSegment()],
    sources: [APIBriefingSource]? = nil,
    nextCursor: String? = nil,
    hasMore: Bool = false
) -> APIBriefingLensResponse {
    APIBriefingLensResponse(
        version: version,
        lens: makeLensSummary(key: key, position: position, tier: tier),
        segments: segments,
        sources: sources ?? [
            APIBriefingSource(
                sourceKey: "content:1",
                kind: "content",
                id: 1,
                title: "Long report",
                summary: "Report summary",
                contentType: .article,
                read: false
            ),
            APIBriefingSource(
                sourceKey: "news:2",
                kind: "news",
                id: 2,
                title: "News item",
                summary: "News summary",
                read: false
            )
        ],
        nextCursor: nextCursor,
        hasMore: hasMore
    )
}

func makeSegment(
    id: Int = 10,
    sourceKeys: [String] = ["content:1"]
) -> APIBriefingSegment {
    APIBriefingSegment(
        id: id,
        createdAt: Date(timeIntervalSince1970: 1_800_000_100),
        status: "active",
        narrationText: "Narration",
        blocks: [
            APIBriefingBlock(
                type: .passage,
                weight: "feature",
                paragraphs: [
                    APIBriefingParagraph(
                        runs: [
                            APIBriefingRun(kind: .text, text: "A useful passage.")
                        ]
                    )
                ]
            )
        ],
        sourceKeys: sourceKeys
    )
}

func makeAudioEpisode(
    id: Int,
    status: APIAudioEpisodeStatus = .completed,
    errorMessage: String? = nil
) -> AudioEpisode {
    AudioEpisode(
        id: id,
        kind: .briefing_narration,
        status: status,
        title: "Briefing",
        sourceCount: 1,
        sourceTitles: ["Long report"],
        errorMessage: errorMessage,
        createdAt: Date(timeIntervalSince1970: 1_800_000_200)
    )
}

func makeBriefingNarration(
    lensKey: String = "today",
    episodeGroupID: String = "briefing-group",
    chapters: [AudioEpisode]
) -> BriefingNarration {
    let firstStatus = chapters.first?.status
    let playable = chapters.first?.isCompleted == true
    let status: APIAudioEpisodeStatus
    if !chapters.isEmpty, chapters.allSatisfy(\.isCompleted) {
        status = .completed
    } else if firstStatus == .failed {
        status = .failed
    } else if playable || chapters.contains(where: \.isGenerating) {
        status = .processing
    } else {
        status = .pending
    }
    return BriefingNarration(
        episodeGroupId: episodeGroupID,
        lensKey: lensKey,
        title: "Today briefing",
        status: status,
        playable: playable,
        durationSeconds: chapters.compactMap(\.durationSeconds).reduce(0, +),
        chapters: chapters
    )
}
