import Foundation
import XCTest
@testable import newsly

@MainActor
final class ReadStateCacheTests: XCTestCase {
    func testMarkReadAndSyncSplitsContentAndNewsEndpoints() async throws {
        let contentRepository = CapturingReadStatusRepository()
        let newsRepository = CapturingReadStatusRepository()
        let cache = ReadStateCache(
            contentReadRepository: contentRepository,
            newsReadRepository: newsRepository,
            badgeStatsStore: makeBadgeStatsStore()
        )

        try await cache.markReadAndSync([
            ReadStateKey(id: 1, contentType: .article),
            ReadStateKey(id: 2, contentType: .podcast),
            ReadStateKey(id: 3, contentType: .news),
        ])

        XCTAssertEqual(contentRepository.markReadCalls, [[1, 2]])
        XCTAssertEqual(newsRepository.markReadCalls, [[3]])
        XCTAssertTrue(cache.isRead(id: 1, contentType: .article))
        XCTAssertTrue(cache.isRead(id: 3, contentType: .news))
    }

    func testMarkReadAndSyncRollsBackWhenSyncFails() async {
        let cache = ReadStateCache(
            contentReadRepository: FailingReadStatusRepository(),
            badgeStatsStore: makeBadgeStatsStore()
        )

        do {
            try await cache.markReadAndSync([
                ReadStateKey(id: 10, contentType: .article),
            ])
            XCTFail("Expected sync failure")
        } catch {
            XCTAssertFalse(cache.isRead(id: 10, contentType: .article))
        }
    }

    func testApplyingProjectsAndFiltersCachedReadState() {
        let cache = ReadStateCache(badgeStatsStore: makeBadgeStatsStore())
        cache.markReadLocally([
            ReadStateKey(id: 20, contentType: .news),
        ], adjustUnreadCounts: false)

        let items = [
            makeSummary(id: 20, contentType: .news),
            makeSummary(id: 21, contentType: .news),
        ]

        XCTAssertEqual(cache.applying(to: items, removeReadItems: false).map(\.isRead), [true, false])
        XCTAssertEqual(cache.applying(to: items, removeReadItems: true).map(\.id), [21])
    }

    private func makeSummary(id: Int, contentType: APIContentType) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: "https://example.com/\(id)",
            title: "Item \(id)",
            source: "Example",
            platform: "Example",
            status: .completed,
            shortSummary: "Summary",
            createdAt: "2026-03-18T05:00:00Z",
            processedAt: "2026-03-18T06:00:00Z",
            classification: nil,
            publicationDate: nil,
            isRead: false,
            isSavedToKnowledge: false,
            imageUrl: nil,
            thumbnailUrl: nil,
            primaryTopic: nil,
            topComment: nil,
            commentCount: nil,
            newsSummary: nil,
            newsKeyPoints: nil
        )
    }

    private func makeBadgeStatsStore() -> BadgeStatsStore {
        BadgeStatsStore(
            notificationCenter: NotificationCenter()
        )
    }
}

private final class CapturingReadStatusRepository: ReadStatusRepositoryType {
    private(set) var markReadCalls: [[Int]] = []

    func markRead(ids: [Int]) async throws {
        markReadCalls.append(ids)
    }
}

private final class FailingReadStatusRepository: ReadStatusRepositoryType {
    func markRead(ids: [Int]) async throws {
        throw URLError(.notConnectedToInternet)
    }
}
