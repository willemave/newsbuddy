import XCTest
@testable import newsly

@MainActor
final class BadgeCountServiceTests: XCTestCase {
    override func tearDown() {
        UnreadCountService.shared.stopPeriodicRefresh(resetCounts: true)
        ProcessingCountService.shared.stopPeriodicRefresh(resetCounts: true)
        super.tearDown()
    }

    func testUnreadCountApplyReturnsFalseForUnchangedCounts() {
        let service = UnreadCountService.shared
        service.stopPeriodicRefresh(resetCounts: true)
        service.applyCounts(UnreadCountsResponse(article: 2, podcast: 1, news: 4))

        let changed = service.applyCounts(UnreadCountsResponse(article: 2, podcast: 1, news: 4))

        XCTAssertFalse(changed)
        XCTAssertEqual(service.articleCount, 2)
        XCTAssertEqual(service.podcastCount, 1)
        XCTAssertEqual(service.newsCount, 4)
    }

    func testProcessingCountApplyReturnsFalseForUnchangedCounts() {
        let service = ProcessingCountService.shared
        service.stopPeriodicRefresh(resetCounts: true)
        service.applyCount(
            ProcessingCountResponse(
                processingCount: 3,
                longFormCount: 2,
                newsCount: 1,
                newsCrawlCount: 1
            )
        )

        let changed = service.applyCount(
            ProcessingCountResponse(
                processingCount: 3,
                longFormCount: 2,
                newsCount: 1,
                newsCrawlCount: 1
            )
        )

        XCTAssertFalse(changed)
        XCTAssertEqual(service.processingCount, 3)
        XCTAssertEqual(service.longFormProcessingCount, 2)
        XCTAssertEqual(service.newsProcessingCount, 1)
        XCTAssertEqual(service.newsCrawlCount, 1)
    }
}
