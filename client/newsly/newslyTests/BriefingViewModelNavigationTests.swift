import XCTest
@testable import newsly

@MainActor
final class BriefingViewModelNavigationTests: XCTestCase {
    func testInitialEntryLoadsOnlySelectedAndPagerNeighbor() async {
        let service = MockBriefingService()
        let summaries = (0..<5).map {
            makeLensSummary(key: "news-\($0)", position: $0)
        }
        service.indexResults = [.value(makeIndex(lenses: summaries), etag: "etag-1")]
        for summary in summaries {
            service.lensResponses[summary.key] = makeLens(key: summary.key, position: summary.position)
        }
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { service.fetchLensKeys.count == 2 }
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(service.fetchLensKeys, ["news-0", "news-1"])
    }

    func testFixedLensDoesNotPrefetchUnrelatedTiers() async {
        let service = MockBriefingService()
        let fixed = makeLensSummary(key: "articles", position: 0, tier: .longform)
        let news = makeLensSummary(key: "news", position: 1)
        service.indexResults = [.value(makeIndex(lenses: [fixed, news]), etag: nil)]
        service.lensResponses["articles"] = makeLens(key: "articles", position: 0, tier: .longform)
        service.lensResponses["news"] = makeLens(key: "news", position: 1)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(service.fetchLensKeys, ["articles"])
    }

    func testLatestIndexResponseIsAuthoritativeEvenWhenVersionIsLower() async {
        let service = MockBriefingService()
        let segment = makeSegment(sourceKeys: ["content:1"])
        service.indexResults = [
            .value(makeIndex(version: 4, lenses: [makeLensSummary(key: "today")]), etag: "etag-4"),
            .value(makeIndex(version: 3, lenses: []), etag: "etag-3")
        ]
        service.lensResponses["today"] = makeLens(key: "today", version: 4, segments: [segment])
        service.readMarkResponse = APIBriefingReadMarkResponse(marked: 1, retired: 0, version: 8)
        let viewModel = BriefingViewModel(service: service)

        await viewModel.loadIndexIfNeeded()
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        viewModel.markSegmentSeen(segment)
        await waitForBriefingCondition(timeoutNanoseconds: 1_500_000_000) {
            viewModel.index?.version == 3
        }

        XCTAssertEqual(viewModel.index?.version, 3)
        XCTAssertEqual(viewModel.index?.lenses.isEmpty, true)
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
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        XCTAssertEqual(viewModel.selectedLensKey, "articles")
        XCTAssertFalse(viewModel.isMastheadCompact)

        viewModel.setHeaderPinned(true, forLens: "articles")
        XCTAssertTrue(viewModel.isMastheadCompact)

        viewModel.selectLens(key: "podcasts")
        XCTAssertFalse(viewModel.isMastheadCompact)

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
        await waitForBriefingCondition { viewModel.selectedLens != nil }

        XCTAssertEqual(viewModel.newsLenses.map(\.key), ["news-tech", "news-world"])
        XCTAssertEqual(viewModel.fixedLenses.map(\.key), ["podcasts", "articles"])
        XCTAssertEqual(viewModel.newsUnreadSourceCount, 4)
        XCTAssertTrue(viewModel.isNewsTierSelected)
        XCTAssertEqual(viewModel.pagerLenses.map(\.key), ["news-tech", "news-world"])

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
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        XCTAssertEqual(viewModel.selectedLensKey, "podcasts")
        XCTAssertFalse(viewModel.isCategoryStripExpanded)

        viewModel.selectNewsTier()
        XCTAssertEqual(viewModel.selectedLensKey, "news-tech")
        XCTAssertTrue(viewModel.isCategoryStripExpanded)

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
        await waitForBriefingCondition { viewModel.selectedLens != nil }
        XCTAssertTrue(viewModel.isCategoryStripExpanded)

        viewModel.setHeaderPinned(true, forLens: "news-tech")
        XCTAssertTrue(viewModel.isMastheadCompact)
        XCTAssertFalse(viewModel.isCategoryStripExpanded)

        viewModel.selectNewsTier()
        XCTAssertEqual(viewModel.selectedLensKey, "news-tech")
        XCTAssertTrue(viewModel.isCategoryStripExpanded)

        viewModel.noteScrolledDown(forLens: "news-tech")
        XCTAssertFalse(viewModel.isCategoryStripExpanded)

        viewModel.setHeaderPinned(false, forLens: "news-tech")
        XCTAssertTrue(viewModel.isCategoryStripExpanded)
    }
}
