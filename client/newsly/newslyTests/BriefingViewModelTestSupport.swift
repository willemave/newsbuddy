import Foundation
import XCTest
@testable import newsly

@MainActor
final class MockBriefingService: BriefingServicing {
    var indexResults: [BriefingIndexFetchResult] = []
    var indexError: Error?
    var indexEtags: [String?] = []
    var fetchIndexDelayNanoseconds: UInt64?
    var lensResponses: [String: APIBriefingLensResponse] = [:]
    var fetchLensDelayNanoseconds: UInt64?
    var fetchLensKeys: [String] = []
    var markReadCalls: [[String]] = []
    var events: [String] = []
    var readMarkResponse = APIBriefingReadMarkResponse(marked: 0, version: 1)
    var markReadError: Error?
    var refreshError: Error?
    var refreshDelayNanoseconds: UInt64?
    var refreshWaitsForResume = false
    private(set) var refreshRequestCount = 0
    var narrationEpisode: AudioEpisode?
    var narrationEpisodes: [AudioEpisode] = []
    var narrationError: Error?
    var narrationRequestDelayNanoseconds: UInt64?
    var narrationWaitsForCancellation = false
    var narrationLensKeys: [String] = []
    private(set) var narrationCancellationCount = 0
    var firstRunCompletionFailuresRemaining = 0
    private(set) var firstRunCompletionCount = 0

    private var refreshContinuation: CheckedContinuation<Void, Never>?

    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult {
        indexEtags.append(etag)
        events.append("fetchIndex:\(etag ?? "nil")")
        if let fetchIndexDelayNanoseconds {
            try? await Task.sleep(nanoseconds: fetchIndexDelayNanoseconds)
        }
        if let indexError {
            throw indexError
        }
        guard !indexResults.isEmpty else {
            return .value(makeIndex(lenses: []), etag: nil)
        }
        return indexResults.removeFirst()
    }

    func fetchLens(key: String) async throws -> APIBriefingLensResponse {
        fetchLensKeys.append(key)
        events.append("fetchLens:\(key)")
        let response = lensResponses[key] ?? makeLens(key: key)
        if let fetchLensDelayNanoseconds {
            try? await Task.sleep(nanoseconds: fetchLensDelayNanoseconds)
        }
        return response
    }

    func markRead(sourceKeys: [String]) async throws -> APIBriefingReadMarkResponse {
        markReadCalls.append(sourceKeys)
        events.append("markRead:\(sourceKeys.joined(separator: ","))")
        if let markReadError {
            throw markReadError
        }
        return readMarkResponse
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

    func digSearch(fragment: String) async throws -> APIBriefingDigSearchResponse {
        APIBriefingDigSearchResponse(results: [], elapsedMs: 0)
    }

    func digSummarize(
        fragment: String,
        passageContext: String,
        results: [APIBriefingDigSearchResult]
    ) async throws -> APIBriefingDigSummarizeResponse {
        APIBriefingDigSummarizeResponse(summary: "Summary", model: "test", elapsedMs: 0)
    }

    func requestNarration(lensKey: String) async throws -> AudioEpisode {
        narrationLensKeys.append(lensKey)
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
        if !narrationEpisodes.isEmpty {
            return narrationEpisodes.removeFirst()
        }
        return narrationEpisode ?? makeAudioEpisode(id: 1)
    }
}

@MainActor
final class MockBriefingAudioEpisodeService: BriefingAudioEpisodeServicing {
    var waitResults: [Result<AudioEpisode, Error>] = []
    private(set) var waitedEpisodeIDs: [Int] = []

    func waitForCompletedEpisode(
        _ episode: AudioEpisode,
        pollIntervalNanoseconds: UInt64,
        maxAttempts: Int
    ) async throws -> AudioEpisode {
        waitedEpisodeIDs.append(episode.id)
        guard !waitResults.isEmpty else { return episode }
        return try waitResults.removeFirst().get()
    }

    func streamResource(for episode: AudioEpisode) async throws -> AuthorizedMediaResource {
        throw AudioEpisodeServiceError.missingStreamResource
    }
}

extension BriefingViewModel {
    convenience init(
        service: BriefingServicing,
        snapshotStore: BriefingSnapshotStoring? = nil,
        refreshPollDelays: [UInt64] = [1_000_000, 2_000_000, 5_000_000],
        firstRunCompletionRetryDelay: UInt64 = 1_000_000
    ) {
        self.init(
            service: service,
            audioEpisodeService: MockBriefingAudioEpisodeService(),
            snapshotStore: snapshotStore,
            refreshPollDelays: refreshPollDelays,
            firstRunCompletionRetryDelay: firstRunCompletionRetryDelay
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
    readyCategoryKeys: [String] = []
) -> APIBriefingFirstRunProgress {
    APIBriefingFirstRunProgress(
        runId: runID,
        revision: revision,
        phase: phase,
        connectedSourceCount: connectedSourceCount,
        completedSources: completedSources,
        activeSources: activeSources,
        readyCategoryKeys: readyCategoryKeys
    )
}

func makeLensSummary(
    key: String,
    title: String = "Today",
    position: Int = 0,
    tier: APIBriefingTier = .news
) -> APIBriefingLensSummary {
    APIBriefingLensSummary(
        key: key,
        tier: tier,
        title: title,
        deck: "Latest unread reporting",
        position: position,
        segmentCount: 1,
        unreadSourceCount: 2
    )
}

func makeLens(
    key: String,
    version: Int = 1,
    position: Int = 0,
    tier: APIBriefingTier = .news,
    segments: [APIBriefingSegment] = [makeSegment()],
    sources: [APIBriefingSource]? = nil
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
        ]
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
