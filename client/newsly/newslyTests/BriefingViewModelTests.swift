import XCTest
@testable import newsly

@MainActor
final class BriefingViewModelTests: XCTestCase {
    func testLoadIndexSelectsFirstLensAndLoadsIt() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "briefing-v1")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertEqual(viewModel.selectedLensKey, "today")
        XCTAssertEqual(service.fetchLensKeys, ["today"])
        XCTAssertEqual(viewModel.selectedLens?.lens.key, "today")
    }

    func testRefreshUsesETagAndKeepsExistingIndexOnNotModified() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]), etag: "etag-4"),
            .notModified
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await viewModel.refreshIndex()

        XCTAssertEqual(service.indexEtags, [nil, "etag-4"])
        XCTAssertEqual(viewModel.index?.version, 4)
        XCTAssertEqual(viewModel.state, .loaded)
    }

    func testMarkSegmentSeenBatchesUnreadSourceKeysAndMarksSourcesReadLocally() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: nil)
        ]
        service.lensResponses["today"] = makeLens(key: "today", segments: [segment])
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 2, version: 8)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)
        try? await Task.sleep(nanoseconds: 450_000_000)

        XCTAssertEqual(service.markReadCalls, [["content:1", "news:2"]])
        XCTAssertEqual(viewModel.index?.version, 8)
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
        XCTAssertEqual(viewModel.source(for: "news:2")?.read, true)
    }

    func testIndexResponseWithoutEtagKeepsPreviousEtag() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]), etag: "etag-4"),
            .value(makeIndex(version: 5, lenses: [makeLensSummary(key: "today")]), etag: nil),
            .notModified
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await viewModel.refreshIndex()
        await viewModel.refreshIndex()

        XCTAssertEqual(service.indexEtags, [nil, "etag-4", "etag-4"])
        XCTAssertEqual(viewModel.index?.version, 5)
    }

    func testStaleIndexResponseDoesNotOverwriteNewerVersion() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]), etag: "etag-4"),
            .value(makeIndex(version: 3, lenses: []), etag: "etag-3")
        ]
        service.lensResponses["today"] = makeLens(key: "today", segments: [segment])
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 1, version: 8)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)
        try? await Task.sleep(nanoseconds: 450_000_000)
        await viewModel.refreshIndex()

        XCTAssertEqual(viewModel.index?.version, 8)
        XCTAssertEqual(viewModel.index?.lenses.isEmpty, false)
    }

    func testSelectLensCarriesPinnedHeaderStateFromPreviousLens() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [makeLensSummary(key: "podcasts"), makeLensSummary(key: "articles")]),
                etag: nil
            )
        ]
        service.lensResponses["podcasts"] = makeLens(key: "podcasts")
        service.lensResponses["articles"] = makeLens(key: "articles")
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        // Equal positions sort by key, so "articles" is auto-selected first.
        XCTAssertEqual(viewModel.selectedLensKey, "articles")

        viewModel.setHeaderPinned(true, forLens: "articles")
        viewModel.selectLens(key: "podcasts")
        XCTAssertTrue(viewModel.carryHeaderPinned)

        viewModel.setHeaderPinned(false, forLens: "podcasts")
        viewModel.selectLens(key: "articles")
        XCTAssertFalse(viewModel.carryHeaderPinned)
    }

    func testCitationLinkedMarkdownWrapsBracketNumbers() {
        let summary = "Fact one [1]. **Bold** fact [12]."

        let linked = BriefingDigViewModel.citationLinkedMarkdown(summary)

        XCTAssertEqual(
            linked,
            "Fact one [\\[1\\]](digsource://1). **Bold** fact [\\[12\\]](digsource://12)."
        )
    }

    func testRequestNarrationStoresEpisodeByLens() async {
        let service = MockBriefingService()
        let episode = makeAudioEpisode(id: 42)
        service.narrationEpisode = episode
        let viewModel = BriefingViewModel(service: service)

        let returned = await viewModel.requestNarration(for: "today")

        XCTAssertEqual(returned?.id, 42)
        XCTAssertEqual(viewModel.narrationEpisode(for: "today")?.id, 42)
        XCTAssertEqual(service.narrationLensKeys, ["today"])
    }

    private func waitFor(
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
}

private final class MockBriefingService: BriefingServicing {
    var indexResults: [BriefingIndexFetchResult] = []
    var indexEtags: [String?] = []
    var lensResponses: [String: APIBriefingLensResponse] = [:]
    var fetchLensKeys: [String] = []
    var markReadCalls: [[String]] = []
    var readMarkResponse = APIBriefingReadMarkResponse(marked: 0, version: 1)
    var narrationEpisode: AudioEpisode?
    var narrationLensKeys: [String] = []

    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult {
        indexEtags.append(etag)
        guard !indexResults.isEmpty else {
            return .value(makeIndex(lenses: []), etag: nil)
        }
        return indexResults.removeFirst()
    }

    func fetchLens(key: String) async throws -> APIBriefingLensResponse {
        fetchLensKeys.append(key)
        return lensResponses[key] ?? makeLens(key: key)
    }

    func markRead(sourceKeys: [String]) async throws -> APIBriefingReadMarkResponse {
        markReadCalls.append(sourceKeys)
        return readMarkResponse
    }

    func requestRefresh() async throws -> APIBriefingRefreshResponse {
        APIBriefingRefreshResponse(enqueued: true, version: 1)
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
        return narrationEpisode ?? makeAudioEpisode(id: 1)
    }
}

private func makeIndex(
    version: Int = 1,
    lenses: [APIBriefingLensSummary]
) -> APIBriefingIndexResponse {
    APIBriefingIndexResponse(
        version: version,
        mastheadTitle: "Today",
        mastheadDeck: "What matters now",
        generatedAt: Date(timeIntervalSince1970: 1_800_000_000),
        lenses: lenses
    )
}

private func makeLensSummary(
    key: String,
    title: String = "Today"
) -> APIBriefingLensSummary {
    APIBriefingLensSummary(
        key: key,
        tier: .news,
        title: title,
        deck: "Latest unread reporting",
        position: 0,
        segmentCount: 1,
        unreadSourceCount: 2
    )
}

private func makeLens(
    key: String,
    segments: [APIBriefingSegment] = [makeSegment()]
) -> APIBriefingLensResponse {
    APIBriefingLensResponse(
        version: 1,
        lens: makeLensSummary(key: key),
        segments: segments,
        sources: [
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

private func makeSegment(
    sourceKeys: [String] = ["content:1"]
) -> APIBriefingSegment {
    APIBriefingSegment(
        id: 10,
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

private func makeAudioEpisode(id: Int) -> AudioEpisode {
    AudioEpisode(
        id: id,
        kind: .briefing_narration,
        status: .completed,
        title: "Briefing",
        sourceCount: 1,
        sourceTitles: ["Long report"],
        createdAt: Date(timeIntervalSince1970: 1_800_000_200)
    )
}
