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

    func testOrderedLensesAreStoredAndRefreshAfterLocalReadMarks() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        let todaySummary = makeLensSummary(key: "today", title: "Today", position: 2)
        let firstSummary = makeLensSummary(key: "first", title: "First", position: 1)
        service.indexResults = [
            .value(makeIndex(lenses: [todaySummary, firstSummary]), etag: nil)
        ]
        service.lensResponses["first"] = makeLens(key: "first", position: 1, segments: [segment])
        service.lensResponses["today"] = makeLens(key: "today", position: 2)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)

        XCTAssertEqual(viewModel.selectedLensKey, "first")
        XCTAssertEqual(viewModel.orderedLenses.map(\.key), ["first", "today"])
        XCTAssertEqual(viewModel.orderedLenses.first?.unreadSourceCount, 0)
    }

    func testConcurrentIndexLoadsShareInFlightRequest() async {
        let service = MockBriefingService()
        service.fetchIndexDelayNanoseconds = 100_000_000
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: "briefing-v1")
        ]
        service.lensResponses["today"] = makeLens(key: "today")
        let viewModel = BriefingViewModel(service: service)

        let first = Task { await viewModel.loadIndexIfNeeded() }
        let second = Task { await viewModel.loadIndexIfNeeded() }
        await first.value
        await second.value
        await waitFor { viewModel.selectedLens != nil }

        XCTAssertEqual(service.indexEtags, [nil])
        XCTAssertEqual(viewModel.state, .loaded)
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
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
        XCTAssertEqual(viewModel.source(for: "news:2")?.read, true)
        // Read segments stay in the feed (greyed by the view); the server
        // retires them and the next index fetch drops them.
        XCTAssertEqual(viewModel.selectedLens?.segments.count, 1)
        XCTAssertEqual(viewModel.index?.lenses.first?.segmentCount, 1)
        XCTAssertEqual(viewModel.index?.lenses.first?.unreadSourceCount, 0)

        try? await Task.sleep(nanoseconds: 450_000_000)

        XCTAssertEqual(service.markReadCalls, [["content:1", "news:2"]])
        XCTAssertEqual(viewModel.index?.version, 8)
    }

    func testMarkSegmentSeenOptimisticallyMarksReadAndDecrementsOwningLensCount() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        service.indexResults = [
            .value(
                makeIndex(lenses: [makeLensSummary(key: "today"), makeLensSummary(key: "later")]),
                etag: nil
            )
        ]
        service.lensResponses["today"] = makeLens(key: "today", segments: [segment])
        service.lensResponses["later"] = APIBriefingLensResponse(
            version: 1,
            lens: makeLensSummary(key: "later"),
            segments: [makeSegment(id: 99, sourceKeys: ["content:9", "news:8"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "content:9",
                    kind: "content",
                    id: 9,
                    title: "Unrelated",
                    summary: nil,
                    contentType: .article,
                    read: false
                ),
                APIBriefingSource(
                    sourceKey: "news:8",
                    kind: "news",
                    id: 8,
                    title: "Unrelated news",
                    summary: nil,
                    read: false
                )
            ]
        )
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.lenses["today"] != nil && viewModel.lenses["later"] != nil }
        viewModel.markSegmentSeen(segment)

        // All UI-facing state updates before any network flush happens.
        XCTAssertEqual(service.markReadCalls, [])
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)
        XCTAssertEqual(viewModel.source(for: "news:2")?.read, true)
        let today = viewModel.index?.lenses.first { $0.key == "today" }
        let later = viewModel.index?.lenses.first { $0.key == "later" }
        XCTAssertEqual(today?.unreadSourceCount, 0)
        XCTAssertEqual(later?.unreadSourceCount, 2)
    }

    func testOptimisticUnreadCountFloorsAtZero() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1", "news:2"])
        let staleSummary = APIBriefingLensSummary(
            key: "today",
            tier: .news,
            title: "Today",
            deck: "Latest unread reporting",
            position: 0,
            segmentCount: 1,
            unreadSourceCount: 1
        )
        service.indexResults = [.value(makeIndex(lenses: [staleSummary]), etag: nil)]
        service.lensResponses["today"] = makeLens(key: "today", segments: [segment])
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)

        XCTAssertEqual(viewModel.index?.lenses.first?.unreadSourceCount, 0)
    }

    func testFailedFlushKeepsLocalReadStateAndRequeuesKeys() async {
        let service = MockBriefingService()
        let first = makeSegment(id: 10, sourceKeys: ["content:1"])
        let second = makeSegment(id: 11, sourceKeys: ["news:2"])
        service.indexResults = [
            .value(makeIndex(lenses: [makeLensSummary(key: "today")]), etag: nil)
        ]
        service.lensResponses["today"] = makeLens(key: "today", segments: [first, second])
        service.markReadError = URLError(.notConnectedToInternet)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(first)
        try? await Task.sleep(nanoseconds: 450_000_000)

        XCTAssertEqual(service.markReadCalls, [["content:1"]])
        XCTAssertEqual(viewModel.source(for: "content:1")?.read, true)

        // The failed keys ride along with the next flush.
        service.markReadError = nil
        viewModel.markSegmentSeen(second)
        try? await Task.sleep(nanoseconds: 450_000_000)

        XCTAssertEqual(service.markReadCalls, [["content:1"], ["content:1", "news:2"]])
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

    func testNewerIndexVersionRefetchesLoadedLens() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 1)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens?.version == 1 }

        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 2,
            segments: [makeSegment(sourceKeys: ["news:2"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "news:2",
                    kind: "news",
                    id: 2,
                    title: "Updated news item",
                    summary: "News summary",
                    read: false,
                    discussion: APIBriefingDiscussion(
                        platform: "hackernews",
                        commentCount: 12,
                        summaryStatus: "completed",
                        overview: "Commenters focused on deployment risk."
                    )
                )
            ]
        )
        await viewModel.refreshIndex()
        await waitFor { viewModel.selectedLens?.version == 2 }

        XCTAssertEqual(service.fetchLensKeys, ["today", "today"])
        XCTAssertEqual(viewModel.source(for: "news:2")?.discussion?.overview, "Commenters focused on deployment risk.")
    }

    func testSameVersionIndexBodyRefetchesLoadedLens() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(makeIndex(version: 8, lenses: [makeLensSummary(key: "today")]), etag: "etag-stale"),
            .value(makeIndex(version: 8, lenses: [makeLensSummary(key: "today")]), etag: "etag-8")
        ]
        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 8,
            segments: [makeSegment(id: 10, sourceKeys: ["content:1"])]
        )
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens?.segments.first?.id == 10 }

        service.lensResponses["today"] = makeLens(
            key: "today",
            version: 8,
            segments: [makeSegment(id: 11, sourceKeys: ["news:2"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "news:2",
                    kind: "news",
                    id: 2,
                    title: "Fresh news item",
                    summary: "Updated summary",
                    read: false
                )
            ]
        )
        await viewModel.refreshIndex()
        await waitFor { viewModel.selectedLens?.segments.first?.id == 11 }

        XCTAssertEqual(service.indexEtags, [nil, "etag-stale"])
        XCTAssertEqual(service.fetchLensKeys, ["today", "today"])
    }

    func testReadMarkFlushKeepsAffectedLensVisibleAndOmitsItFromSnapshot() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore()
        let todaySegment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.indexResults = [
            .value(
                makeIndex(lenses: [
                    makeLensSummary(key: "today", position: 0),
                    makeLensSummary(key: "later", position: 1)
                ]),
                etag: nil
            )
        ]
        service.lensResponses["today"] = makeLens(key: "today", position: 0, segments: [todaySegment])
        service.lensResponses["later"] = makeLens(
            key: "later",
            position: 1,
            segments: [makeSegment(id: 20, sourceKeys: ["content:9"])],
            sources: [
                APIBriefingSource(
                    sourceKey: "content:9",
                    kind: "content",
                    id: 9,
                    title: "Unrelated",
                    summary: nil,
                    contentType: .article,
                    read: false
                )
            ]
        )
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 1, version: 8)
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()
        await waitFor {
            viewModel.lenses["today"] != nil && viewModel.lenses["later"] != nil
        }
        viewModel.markSegmentSeen(todaySegment)

        await waitFor { service.markReadCalls.count == 1 }
        await waitFor(timeoutNanoseconds: 1_500_000_000) {
            snapshotStore.savedSnapshots.contains {
                $0.index.version == 8 && $0.lenses["today"] == nil
            }
        }

        let saved = snapshotStore.savedSnapshots.last { $0.index.version == 8 }
        XCTAssertEqual(viewModel.lenses["today"]?.version, 1)
        XCTAssertEqual(service.fetchLensKeys.filter { $0 == "today" }.count, 1)
        XCTAssertNil(saved?.lenses["today"])
        XCTAssertNotNil(saved?.lenses["later"])
    }

    func testRestoredSnapshotWithMissingLensLoadsItAfterNotModified() async {
        let service = MockBriefingService()
        let snapshotStore = MockBriefingSnapshotStore()
        snapshotStore.snapshotToLoad = BriefingSnapshot(
            index: makeIndex(
                version: 4,
                lenses: [
                    makeLensSummary(key: "alpha", position: 0),
                    makeLensSummary(key: "zeta", position: 1)
                ]
            ),
            etag: "etag-4",
            lenses: ["zeta": makeLens(key: "zeta", version: 4, position: 1)],
            savedAt: Date(timeIntervalSince1970: 1_800_000_000)
        )
        service.indexResults = [.notModified]
        service.lensResponses["alpha"] = makeLens(key: "alpha", version: 4, position: 0)
        let viewModel = BriefingViewModel(service: service, snapshotStore: snapshotStore)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens?.lens.key == "alpha" }

        XCTAssertEqual(service.indexEtags, ["etag-4"])
        XCTAssertEqual(service.fetchLensKeys, ["alpha"])
    }

    func testPullToRefreshFlushesPendingReadMarksBeforeForceLoad() async {
        let service = MockBriefingService()
        let segment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 1, segments: [segment])
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 1, version: 2)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens?.segments.first?.id == 10 }
        viewModel.markSegmentSeen(segment)

        service.lensResponses["today"] = makeLens(key: "today", version: 2, segments: [])
        await viewModel.pullToRefresh()
        await waitFor { viewModel.selectedLens?.version == 2 }

        XCTAssertEqual(service.markReadCalls, [["content:1"]])
        XCTAssertEqual(service.indexEtags, [nil, nil])
        let markReadIndex = service.events.firstIndex(of: "markRead:content:1")
        let refreshIndex = service.events.firstIndex(of: "requestRefresh")
        XCTAssertNotNil(markReadIndex)
        XCTAssertNotNil(refreshIndex)
        XCTAssertLessThan(markReadIndex!, refreshIndex!)
    }

    func testPullToRefreshContinuesWhenReadMarkFlushFails() async {
        let service = MockBriefingService()
        let segment = makeSegment(id: 10, sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]), etag: "etag-1"),
            .value(makeIndex(version: 2, lenses: [makeLensSummary(key: "today")]), etag: "etag-2")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 1, segments: [segment])
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens?.segments.first?.id == 10 }
        viewModel.markSegmentSeen(segment)

        service.markReadError = URLError(.notConnectedToInternet)
        service.lensResponses["today"] = makeLens(key: "today", version: 2, segments: [])
        await viewModel.pullToRefresh()
        await waitFor { viewModel.selectedLens?.version == 2 }

        XCTAssertEqual(service.markReadCalls.first, ["content:1"])
        XCTAssertEqual(service.indexEtags, [nil, nil])
        let markReadIndex = service.events.firstIndex(of: "markRead:content:1")
        let refreshIndex = service.events.firstIndex(of: "requestRefresh")
        XCTAssertNotNil(markReadIndex)
        XCTAssertNotNil(refreshIndex)
        XCTAssertLessThan(markReadIndex!, refreshIndex!)
    }

    func testStaleIndexResponseDoesNotOverwriteNewerVersion() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]), etag: "etag-4"),
            .value(makeIndex(version: 3, lenses: []), etag: "etag-3")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 4, segments: [segment])
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

    func testMastheadCompactTracksScrolledStateOfSelectedLens() async {
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
        XCTAssertFalse(viewModel.isMastheadCompact)

        // Scrolling the selected lens collapses the masthead.
        viewModel.setHeaderPinned(true, forLens: "articles")
        XCTAssertTrue(viewModel.isMastheadCompact)

        // Swiping to a lens still at its top brings the masthead back.
        viewModel.selectLens(key: "podcasts")
        XCTAssertFalse(viewModel.isMastheadCompact)

        // Returning to the scrolled lens collapses it again; scrolling that
        // lens back to the top restores it.
        viewModel.selectLens(key: "articles")
        XCTAssertTrue(viewModel.isMastheadCompact)
        viewModel.setHeaderPinned(false, forLens: "articles")
        XCTAssertFalse(viewModel.isMastheadCompact)
    }

    func testLensesGroupByTierAndPagerScopesToNewsCategories() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [
                    makeLensSummary(key: "news-tech", title: "Tech", position: 1),
                    makeLensSummary(key: "news-world", title: "World", position: 2),
                    makeLensSummary(key: "podcasts", title: "Podcasts", position: 3, tier: .audio),
                    makeLensSummary(key: "articles", title: "Articles", position: 4, tier: .longform)
                ]),
                etag: nil
            )
        ]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }

        XCTAssertEqual(viewModel.newsLenses.map(\.key), ["news-tech", "news-world"])
        XCTAssertEqual(viewModel.fixedLenses.map(\.key), ["podcasts", "articles"])
        XCTAssertEqual(viewModel.newsUnreadSourceCount, 4)
        XCTAssertTrue(viewModel.isNewsTierSelected)
        // In the news tier the pager swipes through categories only.
        XCTAssertEqual(viewModel.pagerLenses.map(\.key), ["news-tech", "news-world"])

        // A fixed lens pages alone — swiping never crosses tiers.
        viewModel.selectLens(key: "podcasts")
        XCTAssertFalse(viewModel.isNewsTierSelected)
        XCTAssertEqual(viewModel.pagerLenses.map(\.key), ["podcasts"])
    }

    func testSelectNewsTierPicksFirstCategoryThenRemembersLastRead() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [
                    makeLensSummary(key: "podcasts", title: "Podcasts", position: 0, tier: .audio),
                    makeLensSummary(key: "news-tech", title: "Tech", position: 1),
                    makeLensSummary(key: "news-world", title: "World", position: 2)
                ]),
                etag: nil
            )
        ]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        XCTAssertEqual(viewModel.selectedLensKey, "podcasts")
        XCTAssertFalse(viewModel.isCategoryStripExpanded)

        // First News tap lands on the first category and reveals the strip.
        viewModel.selectNewsTier()
        XCTAssertEqual(viewModel.selectedLensKey, "news-tech")
        XCTAssertTrue(viewModel.isCategoryStripExpanded)

        // Leaving news and coming back restores the last-read category.
        viewModel.selectLens(key: "news-world")
        viewModel.selectLens(key: "podcasts")
        XCTAssertFalse(viewModel.isCategoryStripExpanded)
        viewModel.selectNewsTier()
        XCTAssertEqual(viewModel.selectedLensKey, "news-world")
    }

    func testCategoryStripCollapsesOnScrollAndReopensOnNewsTap() async {
        let service = MockBriefingService()
        service.indexResults = [
            .value(
                makeIndex(lenses: [
                    makeLensSummary(key: "news-tech", title: "Tech", position: 1),
                    makeLensSummary(key: "news-world", title: "World", position: 2)
                ]),
                etag: nil
            )
        ]
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitFor { viewModel.selectedLens != nil }
        XCTAssertTrue(viewModel.isCategoryStripExpanded)

        // Scrolling into the page collapses the strip with the masthead.
        viewModel.setHeaderPinned(true, forLens: "news-tech")
        XCTAssertTrue(viewModel.isMastheadCompact)
        XCTAssertFalse(viewModel.isCategoryStripExpanded)

        // Tapping News mid-read reopens it without leaving the category…
        viewModel.selectNewsTier()
        XCTAssertEqual(viewModel.selectedLensKey, "news-tech")
        XCTAssertTrue(viewModel.isCategoryStripExpanded)

        // …and the next scroll-down puts it away again.
        viewModel.noteScrolledDown(forLens: "news-tech")
        XCTAssertFalse(viewModel.isCategoryStripExpanded)

        // Back at the top the strip returns on its own.
        viewModel.setHeaderPinned(false, forLens: "news-tech")
        XCTAssertTrue(viewModel.isCategoryStripExpanded)
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

// Main-actor isolated: the view model prefetches every lens concurrently, so
// an unisolated mock races on its recording arrays (fetchLensKeys & co).
@MainActor
private final class MockBriefingService: BriefingServicing {
    var indexResults: [BriefingIndexFetchResult] = []
    var indexEtags: [String?] = []
    var fetchIndexDelayNanoseconds: UInt64?
    var lensResponses: [String: APIBriefingLensResponse] = [:]
    var fetchLensKeys: [String] = []
    var markReadCalls: [[String]] = []
    var events: [String] = []
    var readMarkResponse = APIBriefingReadMarkResponse(marked: 0, version: 1)
    var markReadError: Error?
    var narrationEpisode: AudioEpisode?
    var narrationLensKeys: [String] = []

    func fetchIndex(ifNoneMatch etag: String?) async throws -> BriefingIndexFetchResult {
        indexEtags.append(etag)
        events.append("fetchIndex:\(etag ?? "nil")")
        if let fetchIndexDelayNanoseconds {
            try? await Task.sleep(nanoseconds: fetchIndexDelayNanoseconds)
        }
        guard !indexResults.isEmpty else {
            return .value(makeIndex(lenses: []), etag: nil)
        }
        return indexResults.removeFirst()
    }

    func fetchLens(key: String) async throws -> APIBriefingLensResponse {
        fetchLensKeys.append(key)
        events.append("fetchLens:\(key)")
        return lensResponses[key] ?? makeLens(key: key)
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
        events.append("requestRefresh")
        return APIBriefingRefreshResponse(enqueued: true, version: 1)
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

private final class MockBriefingSnapshotStore: BriefingSnapshotStoring {
    var snapshotToLoad: BriefingSnapshot?
    private(set) var savedSnapshots: [BriefingSnapshot] = []
    private(set) var clearCalls = 0

    func load() -> BriefingSnapshot? {
        snapshotToLoad
    }

    func save(_ snapshot: BriefingSnapshot) {
        savedSnapshots.append(snapshot)
    }

    func clear() {
        clearCalls += 1
        snapshotToLoad = nil
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

private func makeLens(
    key: String,
    version: Int = 1,
    position: Int = 0,
    segments: [APIBriefingSegment] = [makeSegment()],
    sources: [APIBriefingSource]? = nil
) -> APIBriefingLensResponse {
    APIBriefingLensResponse(
        version: version,
        lens: makeLensSummary(key: key, position: position),
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

private func makeSegment(
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
