import Foundation
import XCTest
@testable import newsly

@MainActor
final class TabCoordinatorViewModelTests: XCTestCase {
    func testRootTabReselectRequestsLongFormScrollWithoutRefreshingLoadedLongForm() {
        let shortRepository = FakeContentRepository()
        let longRepository = FakeContentRepository()
        let shortViewModel = ShortNewsListViewModel(
            repository: shortRepository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        let longViewModel = makeLongContentListViewModel(repository: longRepository)
        longViewModel.replaceItems([makeSummary(id: 2, contentType: .article)])
        let coordinator = TabCoordinatorViewModel(
            shortNewsVM: shortViewModel,
            longContentVM: longViewModel,
            briefingVM: makeTabBriefingViewModel(),
            initialTab: .longContent
        )
        var longFormRetapCount = 0
        var shortFormRetapCount = 0
        let selection = RootTabSelectionModel(
            tabCoordinator: coordinator,
            isBriefingExperience: false,
            longFormPathIsEmpty: true,
            shortFormPathIsEmpty: true,
            onLongFormRetap: { longFormRetapCount += 1 },
            onShortFormRetap: { shortFormRetapCount += 1 }
        )

        selection.select(.longContent)

        XCTAssertEqual(coordinator.selectedTab, .longContent)
        XCTAssertEqual(longFormRetapCount, 1)
        XCTAssertEqual(shortFormRetapCount, 0)
        XCTAssertEqual(longRepository.loadPageCallCount, 0)
        XCTAssertEqual(shortRepository.loadPageCallCount, 0)
    }

    func testRootTabReselectRequestsShortFormScrollOnlyAtRoot() {
        let coordinator = makeTabCoordinator(initialTab: .shortNews)
        var longFormRetapCount = 0
        var shortFormRetapCount = 0
        let selection = RootTabSelectionModel(
            tabCoordinator: coordinator,
            isBriefingExperience: false,
            longFormPathIsEmpty: true,
            shortFormPathIsEmpty: true,
            onLongFormRetap: { longFormRetapCount += 1 },
            onShortFormRetap: { shortFormRetapCount += 1 }
        )

        selection.select(.shortNews)

        XCTAssertEqual(coordinator.selectedTab, .shortNews)
        XCTAssertEqual(longFormRetapCount, 0)
        XCTAssertEqual(shortFormRetapCount, 1)
    }

    func testRootTabReselectDoesNotScrollWhenTabPathIsNotAtRoot() {
        let coordinator = makeTabCoordinator(initialTab: .shortNews)
        var longFormRetapCount = 0
        var shortFormRetapCount = 0
        let selection = RootTabSelectionModel(
            tabCoordinator: coordinator,
            isBriefingExperience: false,
            longFormPathIsEmpty: true,
            shortFormPathIsEmpty: false,
            onLongFormRetap: { longFormRetapCount += 1 },
            onShortFormRetap: { shortFormRetapCount += 1 }
        )

        selection.select(.shortNews)

        XCTAssertEqual(longFormRetapCount, 0)
        XCTAssertEqual(shortFormRetapCount, 0)
    }

    func testRootTabBindingAlwaysExposesATabAvailableInTheCurrentExperience() {
        let coordinator = makeTabCoordinator(initialTab: .more)
        let selection = RootTabSelectionModel(
            tabCoordinator: coordinator,
            isBriefingExperience: true,
            longFormPathIsEmpty: true,
            shortFormPathIsEmpty: true,
            onLongFormRetap: {},
            onShortFormRetap: {}
        )

        XCTAssertEqual(selection.binding.wrappedValue, .knowledge)

        selection.reconcile()

        XCTAssertEqual(coordinator.selectedTab, .knowledge)
    }

    func testHandleTabChangeKeepsIncomingLongFormStableWhenAlreadyLoaded() {
        let shortRepository = FakeContentRepository()
        let longRepository = FakeContentRepository()
        let shortViewModel = ShortNewsListViewModel(
            repository: shortRepository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        let longViewModel = makeLongContentListViewModel(repository: longRepository)
        shortViewModel.replaceItems([makeSummary(id: 1, contentType: .news)])
        longViewModel.replaceItems([makeSummary(id: 2, contentType: .article)])

        let coordinator = TabCoordinatorViewModel(
            shortNewsVM: shortViewModel,
            longContentVM: longViewModel,
            briefingVM: makeTabBriefingViewModel(),
            initialTab: .shortNews
        )

        coordinator.handleTabChange(to: .longContent)

        XCTAssertEqual(shortViewModel.currentItems().map(\.id), [1])
        XCTAssertEqual(shortViewModel.state, .idle)
        XCTAssertEqual(longViewModel.currentItems().map(\.id), [2])
        XCTAssertEqual(longViewModel.state, .idle)
        XCTAssertEqual(longRepository.loadPageCallCount, 0)
    }

    func testHandleTabChangeRefreshesIncomingShortNewsWithoutResettingOutgoingLongFormState() async {
        let shortRepository = FakeContentRepository()
        let longRepository = FakeContentRepository()
        let shortViewModel = ShortNewsListViewModel(
            repository: shortRepository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        let longViewModel = makeLongContentListViewModel(repository: longRepository)
        shortViewModel.replaceItems([makeSummary(id: 1, contentType: .news)])
        longViewModel.replaceItems([makeSummary(id: 2, contentType: .article)])

        let coordinator = TabCoordinatorViewModel(
            shortNewsVM: shortViewModel,
            longContentVM: longViewModel,
            briefingVM: makeTabBriefingViewModel(),
            initialTab: .longContent
        )

        coordinator.handleTabChange(to: .shortNews)

        XCTAssertEqual(longViewModel.currentItems().map(\.id), [2])
        XCTAssertEqual(longViewModel.state, .idle)
        await assertEventuallyLoadPageCallCount(1, in: shortRepository)
    }

    func testEnsureUnreadFeedLoadedSkipsReloadWhenItemsAlreadyPresent() async {
        let repository = FakeContentRepository()
        let viewModel = makeLongContentListViewModel(repository: repository)
        viewModel.replaceItems([makeSummary(id: 7, contentType: .article)])

        await viewModel.ensureUnreadFeedLoaded()

        XCTAssertEqual(repository.loadPageCallCount, 0)
    }

    func testEnsureUnreadFeedLoadedRefreshesWhenListIsEmpty() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 7, contentType: .article)]
        )
        let viewModel = makeLongContentListViewModel(repository: repository)

        await viewModel.ensureUnreadFeedLoaded()

        XCTAssertEqual(repository.loadPageCallCount, 1)
        await assertEventuallyLoadedItems([7], in: viewModel)
    }

    func testRefreshUnreadFeedForcesReloadWhenItemsAlreadyPresent() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 9, contentType: .article)]
        )
        let viewModel = makeLongContentListViewModel(repository: repository)
        viewModel.replaceItems([makeSummary(id: 1, contentType: .article)])

        await viewModel.refreshUnreadFeed()

        XCTAssertEqual(repository.loadPageCallCount, 1)
        await assertEventuallyLoadedItems([9], in: viewModel)
    }

    func testLoadNextPageIgnoresDuplicateTriggerWhilePageIsInFlight() async {
        let repository = PendingContentRepository()
        let viewModel = makeLongContentListViewModel(repository: repository)

        let firstLoad = Task { @MainActor in
            await viewModel.loadNextPage()
        }
        await assertEventuallyLoadPageCallCount(1, in: repository)

        let duplicateLoad = Task { @MainActor in
            await viewModel.loadNextPage()
        }
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(repository.loadPageCallCount, 1)
        repository.completeNext()
        await firstLoad.value
        await duplicateLoad.value
    }

    func testCancelledPageRequestDoesNotEnterErrorState() async {
        let repository = FailingContentRepository(error: URLError(.cancelled))
        let viewModel = makeLongContentListViewModel(repository: repository)

        await viewModel.refresh()

        await assertEventuallyState(.idle, in: viewModel)
        if case .error(let error) = viewModel.state {
            XCTFail("Expected cancellation to be ignored, got \(error)")
        }
    }

    func testUnreadRefreshDoesNotReintroduceLocallyReadLongFormItem() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 11, contentType: .article)]
        )
        let viewModel = makeLongContentListViewModel(repository: repository)
        viewModel.replaceItems([makeSummary(id: 11, contentType: .article)])

        await viewModel.markAsRead(11)
        await viewModel.refreshUnreadFeed()

        await assertEventuallyLoadedItems([], in: viewModel)
    }

    func testShortNewsMarkAllVisibleAsReadKeepsItemsVisibleAndMarksNewsRowsRead() async {
        let repository = FakeContentRepository()
        let readRepository = FakeReadStatusRepository()
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: readRepository,
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 11, contentType: .news),
            makeSummary(id: 12, contentType: .news),
        ])

        await viewModel.markAllVisibleAsRead()

        XCTAssertEqual(viewModel.currentItems().map(\.id), [11, 12])
        XCTAssertEqual(viewModel.currentItems().map(\.isRead), [true, true])
        XCTAssertEqual(readRepository.markReadCalls, [[11, 12]])
    }

    func testShortNewsScrollMarkReadKeepsItemVisibleAndMarksItRead() async {
        let repository = FakeContentRepository()
        let readRepository = FakeReadStatusRepository()
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: readRepository,
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 11, contentType: .news),
            makeSummary(id: 12, contentType: .news),
        ])

        viewModel.itemsScrolledPastTop(ids: [11])

        await assertEventuallyShortNewsItems(
            ids: [11, 12],
            readStates: [true, false],
            in: viewModel
        )
        XCTAssertEqual(readRepository.markReadCalls, [[11]])
    }

    func testShortNewsScrollMarkReadDebouncesAndBatchesItems() async {
        let repository = FakeContentRepository()
        let readRepository = FakeReadStatusRepository()
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: readRepository,
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 11, contentType: .news),
            makeSummary(id: 12, contentType: .news),
            makeSummary(id: 13, contentType: .news),
        ])

        viewModel.itemsScrolledPastTop(ids: [12])
        viewModel.itemsScrolledPastTop(ids: [11, 12])

        await assertEventuallyShortNewsItems(
            ids: [11, 12, 13],
            readStates: [true, true, false],
            in: viewModel
        )
        XCTAssertEqual(readRepository.markReadCalls, [[11, 12]])
    }

    func testShortNewsSharedReadCacheKeepsItemVisibleAndMarksItRead() async {
        let repository = FakeContentRepository()
        let readStateCache = ReadStateCache(
            newsReadRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared,
            readStateCache: readStateCache
        )
        viewModel.replaceItems([
            makeSummary(id: 11, contentType: .news),
            makeSummary(id: 12, contentType: .news),
        ])

        readStateCache.markReadLocally([ReadStateKey(id: 11, contentType: .news)])

        await assertEventuallyShortNewsItems(
            ids: [11, 12],
            readStates: [true, false],
            in: viewModel
        )
    }

    func testShortNewsDayGroupsUseContiguousCalendarDays() {
        let viewModel = ShortNewsListViewModel(
            repository: FakeContentRepository(),
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 11, contentType: .news, publicationDate: "2026-03-19T18:00:00Z"),
            makeSummary(id: 12, contentType: .news, publicationDate: "2026-03-19T19:00:00Z"),
            makeSummary(id: 13, contentType: .news, publicationDate: "2026-03-18T18:00:00Z"),
        ])

        XCTAssertEqual(viewModel.dayGroups.map(\.calendarDayKey), ["2026-03-19", "2026-03-18"])
        XCTAssertEqual(viewModel.dayGroups.map(\.delimiterItem.id), [11, 13])
        XCTAssertEqual(viewModel.dayGroups.map { $0.items.map(\.id) }, [[11, 12], [13]])
    }

    func testGroupedShortNewsMarkReadUsesNewsItemEndpoint() async {
        let readRepository = FakeReadStatusRepository()
        let viewModel = NewsGroupViewModel(
            repository: FakeContentRepository(),
            readRepository: readRepository,
            unreadCountService: .shared,
            toastPresenter: StubTabToastPresenter()
        )
        viewModel.newsGroups = [
            NewsGroup(items: [
                makeSummary(id: 21, contentType: .news),
                makeSummary(id: 22, contentType: .news),
            ])
        ]

        await viewModel.markGroupAsRead("21")

        XCTAssertEqual(readRepository.markReadCalls, [[21, 22]])
        XCTAssertTrue(viewModel.newsGroups.first?.isRead ?? false)
    }

    func testGroupedShortNewsLoadUsesUnreadNewsFilter() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 31, contentType: .news)]
        )
        let viewModel = NewsGroupViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared,
            toastPresenter: StubTabToastPresenter()
        )

        await viewModel.loadNewsGroups()

        XCTAssertEqual(repository.loadPageRequests.map(\.contentTypes), [[.news]])
        XCTAssertEqual(repository.loadPageRequests.map(\.readFilter), [.unread])
        XCTAssertEqual(viewModel.newsGroups.flatMap(\.items).map(\.id), [31])
    }

    func testBackgroundRefreshMergesNewItemsOnTopAndKeepsExistingOrder() async {
        // Server returns [3, 1, 2]; the user currently sees [2, 1]. Existing items
        // must keep their on-screen order with the new item surfaced on top.
        let repository = FakeContentRepository(
            responseContents: [
                makeSummary(id: 3, contentType: .news),
                makeSummary(id: 1, contentType: .news),
                makeSummary(id: 2, contentType: .news),
            ]
        )
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 2, contentType: .news),
            makeSummary(id: 1, contentType: .news),
        ])

        await viewModel.refreshInBackgroundAndWait()

        XCTAssertEqual(viewModel.currentItems().map(\.id), [3, 2, 1])
    }

    func testBackgroundRefreshDropsItemsMissingFromServerPage() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 1, contentType: .news)]
        )
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 1, contentType: .news),
            makeSummary(id: 99, contentType: .news),
        ])

        await viewModel.refreshInBackgroundAndWait()

        XCTAssertEqual(viewModel.currentItems().map(\.id), [1])
    }

    func testRefreshInBackgroundAndWaitResumesWhenRequestIsSuperseded() async {
        let repository = PendingContentRepository()
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )

        let resumed = expectation(description: "refresh await resumed")
        Task { @MainActor in
            await viewModel.refreshInBackgroundAndWait()
            resumed.fulfill()
        }

        // Let the background refresh subscribe before superseding it.
        try? await Task.sleep(nanoseconds: 50_000_000)
        let filterTask = Task { @MainActor in
            await viewModel.updateReadFilter(.all)
        }

        await fulfillment(of: [resumed], timeout: 2.0)
        await assertEventuallyLoadPageCallCount(2, in: repository)
        repository.completeAll()
        await filterTask.value
    }

    private func makeSummary(
        id: Int,
        contentType: APIContentType,
        createdAt: String = "2026-03-18T05:00:00Z",
        processedAt: String? = "2026-03-18T06:00:00Z",
        publicationDate: String? = nil
    ) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: "https://example.com/\(id)",
            title: "Item \(id)",
            source: "Example",
            platform: "Example",
            status: .completed,
            shortSummary: "Summary",
            createdAt: createdAt,
            processedAt: processedAt,
            classification: nil,
            publicationDate: publicationDate,
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

    private func makeLongContentListViewModel(
        repository: ContentRepositoryType
    ) -> LongContentListViewModel {
        LongContentListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared,
            contentService: StubContentSummaryListService(),
            toastPresenter: StubTabToastPresenter()
        )
    }

    private func makeTabCoordinator(initialTab: RootTab) -> TabCoordinatorViewModel {
        TabCoordinatorViewModel(
            shortNewsVM: ShortNewsListViewModel(
                repository: FakeContentRepository(),
                readRepository: FakeReadStatusRepository(),
                unreadCountService: .shared
            ),
            longContentVM: makeLongContentListViewModel(repository: FakeContentRepository()),
            briefingVM: makeTabBriefingViewModel(),
            initialTab: initialTab
        )
    }

    private func makeTabBriefingViewModel() -> BriefingViewModel {
        BriefingViewModel(
            service: LiveBriefingService(),
            audioEpisodeService: AudioEpisodeService.shared
        )
    }

    private func assertEventuallyLoadedItems(
        _ expectedIds: [Int],
        in viewModel: LongContentListViewModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if viewModel.currentItems().map(\.id) == expectedIds {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertEqual(viewModel.currentItems().map(\.id), expectedIds, file: file, line: line)
    }

    private func assertEventuallyShortNewsItems(
        ids expectedIds: [Int],
        readStates expectedReadStates: [Bool],
        in viewModel: ShortNewsListViewModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            let items = viewModel.currentItems()
            if items.map(\.id) == expectedIds, items.map(\.isRead) == expectedReadStates {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        let items = viewModel.currentItems()
        XCTAssertEqual(items.map(\.id), expectedIds, file: file, line: line)
        XCTAssertEqual(items.map(\.isRead), expectedReadStates, file: file, line: line)
    }

    private func assertEventuallyState(
        _ expectedState: LoadPhase,
        in viewModel: LongContentListViewModel,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if viewModel.state == expectedState {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertEqual(viewModel.state, expectedState, file: file, line: line)
    }

    private func assertEventuallyLoadPageCallCount(
        _ expectedCount: Int,
        in repository: PendingContentRepository,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if repository.loadPageCallCount == expectedCount {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertEqual(repository.loadPageCallCount, expectedCount, file: file, line: line)
    }

    private func assertEventuallyLoadPageCallCount(
        _ expectedCount: Int,
        in repository: FakeContentRepository,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<50 {
            if repository.loadPageCallCount == expectedCount {
                return
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertEqual(repository.loadPageCallCount, expectedCount, file: file, line: line)
    }
}

private final class PendingContentRepository: ContentRepositoryType, @unchecked Sendable {
    private struct PendingRequest {
        let id: UUID
        let continuation: CheckedContinuation<ContentListResponse, Error>
    }

    private let lock = NSLock()
    private var pendingRequests: [PendingRequest] = []
    private var storedLoadPageCallCount = 0

    var loadPageCallCount: Int {
        withLock { storedLoadPageCallCount }
    }

    func loadPage(
        contentTypes: [APIContentType],
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) async throws -> ContentListResponse {
        let id = UUID()
        withLock {
            storedLoadPageCallCount += 1
        }

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<ContentListResponse, Error>) in
                withLock {
                    pendingRequests.append(PendingRequest(id: id, continuation: continuation))
                }
                if Task.isCancelled {
                    cancel(id: id)
                }
            }
        } onCancel: { [weak self] in
            self?.cancel(id: id)
        }
    }

    func loadDetail(id: Int) async throws -> ContentDetail {
        fatalError("unused in test")
    }

    func completeNext(contents: [ContentSummary] = []) {
        let request: PendingRequest? = withLock {
            pendingRequests.isEmpty ? nil : pendingRequests.removeFirst()
        }
        request?.continuation.resume(returning: makeResponse(contents: contents))
    }

    func completeAll(contents: [ContentSummary] = []) {
        let requests = withLock {
            let requests = pendingRequests
            pendingRequests.removeAll()
            return requests
        }
        let response = makeResponse(contents: contents)
        requests.forEach { $0.continuation.resume(returning: response) }
    }

    private func cancel(id: UUID) {
        let request: PendingRequest? = withLock {
            guard let index = pendingRequests.firstIndex(where: { $0.id == id }) else { return nil }
            return pendingRequests.remove(at: index)
        }
        request?.continuation.resume(throwing: CancellationError())
    }

    private func makeResponse(contents: [ContentSummary]) -> ContentListResponse {
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

    private func withLock<T>(_ operation: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return operation()
    }
}

private final class FailingContentRepository: ContentRepositoryType {
    private let error: Error
    private(set) var loadPageCallCount = 0

    init(error: Error) {
        self.error = error
    }

    func loadPage(
        contentTypes: [APIContentType],
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) async throws -> ContentListResponse {
        loadPageCallCount += 1
        throw error
    }

    func loadDetail(id: Int) async throws -> ContentDetail {
        fatalError("unused in test")
    }
}

private final class FakeContentRepository: ContentRepositoryType {
    struct LoadPageRequest {
        let contentTypes: [APIContentType]
        let readFilter: ReadFilter
        let cursor: String?
        let limit: Int?
    }

    private let responseContents: [ContentSummary]
    private(set) var loadPageRequests: [LoadPageRequest] = []
    var loadPageCallCount: Int { loadPageRequests.count }

    init(responseContents: [ContentSummary] = []) {
        self.responseContents = responseContents
    }

    func loadPage(
        contentTypes: [APIContentType],
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) async throws -> ContentListResponse {
        loadPageRequests.append(
            LoadPageRequest(
                contentTypes: contentTypes,
                readFilter: readFilter,
                cursor: cursor,
                limit: limit
            )
        )
        return ContentListResponse(
            contents: responseContents,
            availableDates: [],
            contentTypes: contentTypes.map(\.rawValue),
            meta: PaginationMetadata(
                nextCursor: nil,
                hasMore: false,
                pageSize: responseContents.count,
                total: responseContents.count
            )
        )
    }

    func loadDetail(id: Int) async throws -> ContentDetail {
        fatalError("unused in test")
    }
}

private enum StubContentSummaryListServiceError: Error {
    case unexpectedCall
}

private final class StubContentSummaryListService: ContentSummaryListServicing {
    func fetchContentList(
        contentTypes: [String]?,
        date: String?,
        readFilter: String,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw StubContentSummaryListServiceError.unexpectedCall
    }

    func fetchContentList(
        contentType: String?,
        date: String?,
        readFilter: String,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw StubContentSummaryListServiceError.unexpectedCall
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw StubContentSummaryListServiceError.unexpectedCall
    }

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        throw StubContentSummaryListServiceError.unexpectedCall
    }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw StubContentSummaryListServiceError.unexpectedCall
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw StubContentSummaryListServiceError.unexpectedCall
    }

    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse {
        throw StubContentSummaryListServiceError.unexpectedCall
    }

    func markContentAsUnread(id: Int) async throws {
        throw StubContentSummaryListServiceError.unexpectedCall
    }
}

@MainActor
private final class StubTabToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}

private final class FakeReadStatusRepository: ReadStatusRepositoryType {
    private(set) var markReadCalls: [[Int]] = []

    func markRead(ids: [Int]) async throws {
        markReadCalls.append(ids)
    }
}
