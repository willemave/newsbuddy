import Combine
import XCTest
@testable import newsly

@MainActor
final class BadgeCountServiceTests: XCTestCase {
    private var cancellables: Set<AnyCancellable> = []

    override func tearDown() {
        UnreadCountService.shared.stopPeriodicRefresh(resetCounts: true)
        ProcessingCountService.shared.stopPeriodicRefresh(resetCounts: true)
        cancellables.removeAll()
        super.tearDown()
    }

    func testUnreadCountApplySkipsUnchangedAssignments() {
        let service = UnreadCountService.shared
        service.stopPeriodicRefresh(resetCounts: true)
        service.applyCounts(UnreadCountsResponse(article: 2, podcast: 1, news: 4))

        var objectWillChangeCount = 0
        service.objectWillChange
            .sink { objectWillChangeCount += 1 }
            .store(in: &cancellables)

        let changed = service.applyCounts(UnreadCountsResponse(article: 2, podcast: 1, news: 4))

        XCTAssertFalse(changed)
        XCTAssertEqual(objectWillChangeCount, 0)
    }

    func testProcessingCountApplySkipsUnchangedAssignments() {
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

        var objectWillChangeCount = 0
        service.objectWillChange
            .sink { objectWillChangeCount += 1 }
            .store(in: &cancellables)

        let changed = service.applyCount(
            ProcessingCountResponse(
                processingCount: 3,
                longFormCount: 2,
                newsCount: 1,
                newsCrawlCount: 1
            )
        )

        XCTAssertFalse(changed)
        XCTAssertEqual(objectWillChangeCount, 0)
    }
}
