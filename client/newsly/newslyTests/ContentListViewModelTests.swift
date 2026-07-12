import XCTest
@testable import newsly

@MainActor
final class ContentListViewModelTests: XCTestCase {
    func testRecentlyReadKeepsReadItemsWhenDefaultFeedFilterIsUnread() async {
        let service = RecentlyReadContentService(contents: [makeReadSummary(id: 42)])
        let viewModel = ContentListViewModel(
            contentService: service,
            unreadCountService: .shared,
            readStateCache: ReadStateCache()
        )

        await viewModel.loadRecentlyRead()

        XCTAssertEqual(viewModel.contents.map(\.id), [42])
    }

    private func makeReadSummary(id: Int) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: .article,
            url: "https://example.com/\(id)",
            title: "Recently read item",
            source: "Example",
            platform: "Example",
            status: .completed,
            shortSummary: "Summary",
            createdAt: "2026-07-12T12:00:00Z",
            processedAt: "2026-07-12T12:01:00Z",
            classification: nil,
            publicationDate: nil,
            isRead: true,
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
}

private enum RecentlyReadContentServiceError: Error {
    case unexpectedCall
}

private final class RecentlyReadContentService: ContentSummaryListServicing {
    let contents: [ContentSummary]

    init(contents: [ContentSummary]) {
        self.contents = contents
    }

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        ContentListResponse(
            contents: contents,
            availableDates: [],
            contentTypes: [],
            meta: PaginationMetadata(
                nextCursor: nil,
                hasMore: false,
                pageSize: contents.count,
                total: contents.count
            )
        )
    }

    func fetchContentList(
        contentTypes: [String]?,
        date: String?,
        readFilter: String,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func fetchContentList(
        contentType: String?,
        date: String?,
        readFilter: String,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func markContentAsUnread(id: Int) async throws {
        throw RecentlyReadContentServiceError.unexpectedCall
    }
}
