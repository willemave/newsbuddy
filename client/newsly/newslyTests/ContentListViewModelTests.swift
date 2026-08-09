import XCTest
@testable import newsly

@MainActor
final class ContentListViewModelTests: XCTestCase {
    func testRecentlyReadKeepsReadItems() async {
        let service = RecentlyReadContentService(contents: [makeReadSummary(id: 42)])
        let viewModel = ContentListViewModel(
            contentService: service,
            readStateCache: ReadStateCache()
        )

        await viewModel.loadRecentlyRead()

        XCTAssertEqual(viewModel.contents.map(\.id), [42])
    }

    func testReadyContentIDsStaySynchronizedWithContentMutations() {
        let viewModel = ContentListViewModel(
            contentService: RecentlyReadContentService(contents: []),
            readStateCache: ReadStateCache()
        )

        viewModel.contents = [
            makeReadSummary(id: 1, status: .completed),
            makeReadSummary(id: 2, status: .processing),
            makeReadSummary(id: 3, status: .failed),
        ]
        XCTAssertEqual(viewModel.readyContentIDs, [1])

        viewModel.contents = [
            makeReadSummary(id: 2, status: .completed),
            makeReadSummary(id: 1, status: .completed),
        ]
        XCTAssertEqual(viewModel.readyContentIDs, [2, 1])

        viewModel.contents = []
        XCTAssertTrue(viewModel.readyContentIDs.isEmpty)
    }

    func testSwitchingModesDropsAnInFlightKnowledgeAppend() async {
        let service = ModeSwapContentService()
        let viewModel = ContentListViewModel(
            contentService: service,
            readStateCache: ReadStateCache()
        )

        await viewModel.loadKnowledgeLibrary(query: "chips")
        XCTAssertEqual(viewModel.contents.map(\.id), [1])

        let appendTask = Task { await viewModel.loadMoreContent() }
        await waitUntil { service.knowledgeAppendStarted }
        await viewModel.loadRecentlyRead()
        await appendTask.value

        XCTAssertEqual(viewModel.contents.map(\.id), [2])
    }

    func testStaleReloadDoesNotUndoInFlightKnowledgeSave() async {
        let content = makeReadSummary(id: 42)
        let service = StaleKnowledgeMutationContentService(content: content)
        let viewModel = ContentListViewModel(
            contentService: service,
            readStateCache: ReadStateCache()
        )
        await viewModel.loadKnowledgeLibrary()

        service.pauseSaveResponse()
        let saveTask = Task { await viewModel.toggleKnowledgeSave(42) }
        await waitUntil { service.saveResponsePaused }

        await viewModel.loadKnowledgeLibrary()
        XCTAssertEqual(viewModel.contents.first?.isSavedToKnowledge, true)

        service.resumeSaveResponse()
        await saveTask.value
        XCTAssertEqual(viewModel.contents.first?.isSavedToKnowledge, true)
    }

    func testAuthoritativeAbsenceRetiresOldUnsavedMutationBeforeExternalSave() async {
        let service = KnowledgeRemovalReconciliationService(
            contents: [makeReadSummary(id: 42, isSavedToKnowledge: true)]
        )
        let viewModel = ContentListViewModel(
            contentService: service,
            readStateCache: ReadStateCache()
        )
        await viewModel.loadKnowledgeLibrary()

        await viewModel.toggleKnowledgeSave(42)
        XCTAssertEqual(viewModel.contents.first?.isSavedToKnowledge, false)

        service.contents = []
        await viewModel.loadKnowledgeLibrary()
        XCTAssertTrue(viewModel.contents.isEmpty)

        service.contents = [makeReadSummary(id: 42, isSavedToKnowledge: true)]
        await viewModel.loadKnowledgeLibrary()
        XCTAssertEqual(viewModel.contents.first?.isSavedToKnowledge, true)
    }

    private func waitUntil(
        _ predicate: @escaping () -> Bool,
        attempts: Int = 100
    ) async {
        for _ in 0..<attempts {
            if predicate() { return }
            try? await Task.sleep(for: .milliseconds(1))
        }
        XCTFail("Condition was not satisfied before timeout")
    }

    private func makeReadSummary(
        id: Int,
        status: APIContentStatus = .completed,
        isSavedToKnowledge: Bool = false
    ) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: .article,
            url: "https://example.com/\(id)",
            title: "Recently read item",
            source: "Example",
            platform: "Example",
            status: status,
            shortSummary: "Summary",
            createdAt: "2026-07-12T12:00:00Z",
            processedAt: "2026-07-12T12:01:00Z",
            classification: nil,
            publicationDate: nil,
            isRead: true,
            isSavedToKnowledge: isSavedToKnowledge,
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

private final class KnowledgeRemovalReconciliationService: ContentSummaryListServicing {
    var contents: [ContentSummary]

    init(contents: [ContentSummary]) {
        self.contents = contents
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        _ = (query, cursor, limit)
        return ContentListResponse(
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

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        KnowledgeMutationResponse(
            status: .success,
            contentId: id,
            isSavedToKnowledge: false,
            message: "Removed"
        )
    }

    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func markContentAsUnread(id: Int) async throws {
        throw RecentlyReadContentServiceError.unexpectedCall
    }
}

private final class StaleKnowledgeMutationContentService: ContentSummaryListServicing {
    private let content: ContentSummary
    private let saveStateLock = NSLock()
    private var shouldPauseSave = false
    private var shouldKeepSavePaused = false

    var saveResponsePaused: Bool {
        saveStateLock.withLock { shouldKeepSavePaused }
    }

    init(content: ContentSummary) {
        self.content = content
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        _ = (query, cursor, limit)
        return ContentListResponse(
            contents: [content],
            availableDates: [],
            contentTypes: [],
            meta: PaginationMetadata(
                nextCursor: nil,
                hasMore: false,
                pageSize: 1,
                total: 1
            )
        )
    }

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        let shouldPause = saveStateLock.withLock {
            let result = shouldPauseSave
            shouldPauseSave = false
            shouldKeepSavePaused = result
            return result
        }
        while shouldPause, saveStateLock.withLock({ shouldKeepSavePaused }) {
            try await Task.sleep(for: .milliseconds(1))
        }
        return KnowledgeMutationResponse(
            status: .success,
            contentId: id,
            isSavedToKnowledge: true,
            message: "Saved"
        )
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

    func pauseSaveResponse() {
        saveStateLock.withLock { shouldPauseSave = true }
    }

    func resumeSaveResponse() {
        saveStateLock.withLock { shouldKeepSavePaused = false }
    }
}

private final class ModeSwapContentService: ContentSummaryListServicing {
    private let stateLock = NSLock()
    private var didStartKnowledgeAppend = false

    var knowledgeAppendStarted: Bool {
        stateLock.withLock { didStartKnowledgeAppend }
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        _ = (query, limit)
        if cursor == nil {
            return Self.response(contents: [Self.summary(id: 1)], nextCursor: "knowledge-next")
        }

        stateLock.withLock { didStartKnowledgeAppend = true }
        try? await Task.sleep(for: .milliseconds(50))
        return Self.response(contents: [Self.summary(id: 3)], nextCursor: nil)
    }

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        _ = (contentType, date, cursor, limit)
        return Self.response(contents: [Self.summary(id: 2)], nextCursor: nil)
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

    private static func response(
        contents: [ContentSummary],
        nextCursor: String?
    ) -> ContentListResponse {
        ContentListResponse(
            contents: contents,
            availableDates: [],
            contentTypes: [],
            meta: PaginationMetadata(
                nextCursor: nextCursor,
                hasMore: nextCursor != nil,
                pageSize: contents.count,
                total: contents.count
            )
        )
    }

    private static func summary(id: Int) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: .article,
            url: "https://example.com/\(id)",
            title: "Item \(id)",
            source: "Example",
            platform: "Example",
            status: .completed,
            shortSummary: "Summary",
            createdAt: "2026-07-12T12:00:00Z",
            processedAt: "2026-07-12T12:01:00Z",
            classification: nil,
            publicationDate: nil,
            isRead: true,
            isSavedToKnowledge: true,
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
