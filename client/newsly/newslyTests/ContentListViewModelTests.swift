import XCTest
@testable import newsly

@MainActor
final class ContentListViewModelTests: XCTestCase {
    func testRecentlyReadKeepsReadItems() async {
        let service = RecentlyReadContentService(contents: [makeReadSummary(id: 42)])
        let viewModel = ContentListViewModel(
            contentService: service,
            readStateCache: makeTestReadStateCache()
        )

        await viewModel.loadRecentlyRead()

        XCTAssertEqual(viewModel.contents.map(\.id), [42])
    }

    func testReadyContentIDsStaySynchronizedWithContentMutations() {
        let viewModel = ContentListViewModel(
            contentService: RecentlyReadContentService(contents: []),
            readStateCache: makeTestReadStateCache()
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
            readStateCache: makeTestReadStateCache()
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
            readStateCache: makeTestReadStateCache()
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
            readStateCache: makeTestReadStateCache()
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

    func testAcceptedSaveAndRemovalFenceBufferedRefreshPages() async {
        for initiallySaved in [false, true] {
            let first = makeReadSummary(id: 42, isSavedToKnowledge: initiallySaved,
                                       knowledgeSavedAt: "2026-10-18T12:00:00Z")
            let last = makeReadSummary(id: 41, isSavedToKnowledge: true,
                                      knowledgeSavedAt: "2026-10-17T12:00:00Z")
            let service = KnowledgeRemovalReconciliationService(contents: [first, last])
            let model = ContentListViewModel(contentService: service, readStateCache: makeTestReadStateCache())
            await model.loadKnowledgeLibrary()

            var pendingPage: CheckedContinuation<ContentListResponse, Never>?
            service.pageLoader = { cursor in
                if cursor == nil { return self.page([first], nextCursor: "tail") }
                // Deliberately ignore cancellation to exercise the generation fence.
                return await withCheckedContinuation { pendingPage = $0 }
            }
            service.mutationHandler = { id, saved in
                KnowledgeMutationResponse(status: .success, contentId: id,
                                          isSavedToKnowledge: saved, message: "Updated")
            }
            let refresh = Task { await model.revalidateKnowledgeLibrary(preservingLoadedPages: true) }
            await waitUntil { pendingPage != nil }

            await model.toggleKnowledgeSave(42)
            XCTAssertFalse(model.isReadInFlight)
            XCTAssertEqual(model.contents.first?.isSavedToKnowledge, !initiallySaved)
            pendingPage?.resume(returning: page([last]))
            let refreshed = await refresh.value

            XCTAssertFalse(refreshed)
            XCTAssertEqual(model.contents.map(\.id), [42, 41])
            XCTAssertEqual(model.contents.first?.isSavedToKnowledge, !initiallySaved)
            XCTAssertFalse(model.isLoading)
            XCTAssertNil(model.initialLoadErrorMessage)
        }
    }

    func testFailedRemovalFencesRefreshStartedDuringOptimisticMutation() async {
        let first = makeReadSummary(id: 42, isSavedToKnowledge: true,
                                   knowledgeSavedAt: "2026-10-18T12:00:00Z")
        let last = makeReadSummary(id: 41, isSavedToKnowledge: true,
                                  knowledgeSavedAt: "2026-10-17T12:00:00Z")
        let service = KnowledgeRemovalReconciliationService(contents: [first, last])
        let model = ContentListViewModel(contentService: service, readStateCache: makeTestReadStateCache())
        await model.loadKnowledgeLibrary()

        var pendingMutation: CheckedContinuation<KnowledgeMutationResponse, Error>?
        service.mutationHandler = { _, _ in
            try await withCheckedThrowingContinuation { pendingMutation = $0 }
        }
        let removal = Task { await model.toggleKnowledgeSave(42) }
        await waitUntil { pendingMutation != nil }
        XCTAssertEqual(model.contents.first?.isSavedToKnowledge, false)

        var pendingPage: CheckedContinuation<ContentListResponse, Never>?
        service.pageLoader = { cursor in
            if cursor == nil { return self.page([first], nextCursor: "tail") }
            return await withCheckedContinuation { pendingPage = $0 }
        }
        let refresh = Task { await model.revalidateKnowledgeLibrary(preservingLoadedPages: true) }
        await waitUntil { pendingPage != nil }
        pendingMutation?.resume(throwing: RecentlyReadContentServiceError.unexpectedCall)
        await removal.value
        XCTAssertFalse(model.isReadInFlight)

        pendingPage?.resume(returning: page([last]))
        let refreshed = await refresh.value
        XCTAssertFalse(refreshed)
        XCTAssertEqual(model.contents.first?.isSavedToKnowledge, true)
        XCTAssertEqual(model.contents.map(\.id), [42, 41])
        XCTAssertFalse(model.isLoading)
        XCTAssertNotNil(model.actionErrorMessage)
        XCTAssertNil(model.initialLoadErrorMessage)
    }

    func testAcceptedSaveDoesNotCancelReplacementInitialLoad() async {
        let first = makeReadSummary(id: 42)
        let service = KnowledgeRemovalReconciliationService(contents: [first])
        let model = ContentListViewModel(contentService: service, readStateCache: makeTestReadStateCache())
        await model.loadKnowledgeLibrary()

        var pendingMutation: CheckedContinuation<KnowledgeMutationResponse, Error>?
        service.mutationHandler = { _, _ in
            try await withCheckedThrowingContinuation { pendingMutation = $0 }
        }
        let save = Task { await model.toggleKnowledgeSave(42) }
        await waitUntil { pendingMutation != nil }

        var pendingPage: CheckedContinuation<ContentListResponse, Never>?
        service.pageLoader = { _ in
            await withCheckedContinuation { pendingPage = $0 }
        }
        let replacement = Task { await model.loadKnowledgeLibrary(query: "new search") }
        await waitUntil { pendingPage != nil }
        XCTAssertTrue(model.contents.isEmpty)

        pendingMutation?.resume(returning: KnowledgeMutationResponse(
            status: .success, contentId: 42, isSavedToKnowledge: true, message: "Saved"
        ))
        await save.value
        XCTAssertTrue(model.isReadInFlight)
        XCTAssertTrue(model.isLoading)

        pendingPage?.resume(returning: page([first]))
        await replacement.value
        XCTAssertEqual(model.contents.first?.isSavedToKnowledge, true)
        XCTAssertFalse(model.isLoading)
        XCTAssertNil(model.initialLoadErrorMessage)
    }

    private func page(_ contents: [ContentSummary], nextCursor: String? = nil) -> ContentListResponse {
        ContentListResponse(contents: contents, availableDates: [], contentTypes: [],
                            meta: PaginationMetadata(nextCursor: nextCursor, hasMore: nextCursor != nil,
                                                     pageSize: contents.count, total: nil))
    }

    func testReprocessingUnavailableSaveReloadsItsPreparingState() async {
        let service = KnowledgeRemovalReconciliationService(
            contents: [makeReadSummary(id: 42, status: .failed, isSavedToKnowledge: true)]
        )
        service.reprocessedContent = makeReadSummary(id: 42, status: .new, isSavedToKnowledge: true)
        let viewModel = ContentListViewModel(
            contentService: service,
            readStateCache: makeTestReadStateCache()
        )
        await viewModel.loadKnowledgeLibrary()
        XCTAssertEqual(viewModel.contents.first?.savedLibraryItemState, .unavailable)

        let succeeded = await viewModel.reprocessKnowledgeItem(42)

        XCTAssertTrue(succeeded)
        XCTAssertEqual(service.reprocessedIDs, [42])
        XCTAssertEqual(viewModel.contents.first?.savedLibraryItemState, .processing)
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

    func testKnowledgeRefreshDoesNotUseIDsToOrderRoundedTimestampTies() async {
        let boundary = makeReadSummary(id: 99, knowledgeSavedAt: "2026-10-18T12:00:00.123Z")
        let newer = makeReadSummary(id: 1, knowledgeSavedAt: "2026-10-18T12:00:00.123Z")
        let older = makeReadSummary(id: 100, knowledgeSavedAt: "2026-10-18T12:00:00.122Z")
        for boundaryExists in [true, false] {
            let service = RecentlyReadContentService(contents: [])
            service.knowledgePages = [
                "": ContentListResponse(contents: [newer], availableDates: [], contentTypes: [],
                    meta: PaginationMetadata(nextCursor: "tail", hasMore: true, pageSize: 1, total: nil)),
                "tail": ContentListResponse(contents: boundaryExists ? [boundary] : [older],
                    availableDates: [], contentTypes: [],
                    meta: PaginationMetadata(nextCursor: "older", hasMore: true, pageSize: 1, total: nil))
            ]
            let model = ContentListViewModel(contentService: service, readStateCache: makeTestReadStateCache())
            model.contents = [boundary]
            let refreshed = await model.revalidateKnowledgeLibrary(preservingLoadedPages: true)
            XCTAssertTrue(refreshed)
            XCTAssertEqual(model.contents.map(\.id), boundaryExists ? [1, 99] : [1, 100])
        }
    }

    private func makeReadSummary(
        id: Int,
        status: APIContentStatus = .completed,
        isSavedToKnowledge: Bool = false,
        knowledgeSavedAt: String? = nil
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
            isRead: true,
            isSavedToKnowledge: isSavedToKnowledge,
            knowledgeSavedAt: knowledgeSavedAt,
            imageUrl: nil,
            thumbnailUrl: nil,
        )
    }
}

private final class KnowledgeRemovalReconciliationService: ContentSummaryListServicing {
    var contents: [ContentSummary]
    var reprocessedContent: ContentSummary?
    var pageLoader: ((String?) async -> ContentListResponse)?
    var mutationHandler: ((Int, Bool) async throws -> KnowledgeMutationResponse)?
    private(set) var reprocessedIDs: [Int] = []

    init(contents: [ContentSummary]) {
        self.contents = contents
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        if let pageLoader { return await pageLoader(cursor) }
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
        if let mutationHandler { return try await mutationHandler(id, true) }
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        if let mutationHandler { return try await mutationHandler(id, false) }
        return KnowledgeMutationResponse(
            status: .success,
            contentId: id,
            isSavedToKnowledge: false,
            message: "Removed"
        )
    }

    func reprocessKnowledgeItem(id: Int) async throws -> KnowledgeMutationResponse {
        reprocessedIDs.append(id)
        if let reprocessedContent {
            contents = [reprocessedContent]
        }
        return KnowledgeMutationResponse(
            status: .success,
            contentId: id,
            isSavedToKnowledge: true,
            message: "Reprocessing started"
        )
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

    func reprocessKnowledgeItem(id: Int) async throws -> KnowledgeMutationResponse {
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

    func reprocessKnowledgeItem(id: Int) async throws -> KnowledgeMutationResponse {
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
            isRead: true,
            isSavedToKnowledge: true,
            imageUrl: nil,
            thumbnailUrl: nil,
        )
    }
}

private enum RecentlyReadContentServiceError: Error {
    case unexpectedCall
}

private final class RecentlyReadContentService: ContentSummaryListServicing {
    let contents: [ContentSummary]
    var knowledgePages: [String: ContentListResponse] = [:]

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
        guard let page = knowledgePages[cursor ?? ""] else {
            throw RecentlyReadContentServiceError.unexpectedCall
        }
        return page
    }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func reprocessKnowledgeItem(id: Int) async throws -> KnowledgeMutationResponse {
        throw RecentlyReadContentServiceError.unexpectedCall
    }

    func markContentAsUnread(id: Int) async throws {
        throw RecentlyReadContentServiceError.unexpectedCall
    }
}
