import Combine
import XCTest
@testable import newsly

@MainActor
final class TabCoordinatorViewModelTests: XCTestCase {
    func testHandleTabChangeKeepsIncomingLongFormStableWhenAlreadyLoaded() {
        let shortRepository = FakeContentRepository()
        let longRepository = FakeContentRepository()
        let shortViewModel = ShortNewsListViewModel(
            repository: shortRepository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        let longViewModel = LongContentListViewModel(
            repository: longRepository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        shortViewModel.replaceItems([makeSummary(id: 1, contentType: "news")])
        longViewModel.replaceItems([makeSummary(id: 2, contentType: "article")])

        let coordinator = TabCoordinatorViewModel(
            shortNewsVM: shortViewModel,
            longContentVM: longViewModel,
            initialTab: .shortNews
        )

        coordinator.handleTabChange(to: .longContent)

        XCTAssertEqual(shortViewModel.currentItems().map(\.id), [1])
        XCTAssertEqual(shortViewModel.state, .idle)
        XCTAssertEqual(longViewModel.currentItems().map(\.id), [2])
        XCTAssertEqual(longViewModel.state, .idle)
        XCTAssertEqual(longRepository.loadPageCallCount, 0)
    }

    func testHandleTabChangeRefreshesIncomingShortNewsWithoutResettingOutgoingLongFormState() {
        let shortRepository = FakeContentRepository()
        let longRepository = FakeContentRepository()
        let shortViewModel = ShortNewsListViewModel(
            repository: shortRepository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        let longViewModel = LongContentListViewModel(
            repository: longRepository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        shortViewModel.replaceItems([makeSummary(id: 1, contentType: "news")])
        longViewModel.replaceItems([makeSummary(id: 2, contentType: "article")])

        let coordinator = TabCoordinatorViewModel(
            shortNewsVM: shortViewModel,
            longContentVM: longViewModel,
            initialTab: .longContent
        )

        coordinator.handleTabChange(to: .shortNews)

        XCTAssertEqual(longViewModel.currentItems().map(\.id), [2])
        XCTAssertEqual(longViewModel.state, .idle)
        XCTAssertEqual(shortRepository.loadPageCallCount, 1)
    }

    func testEnsureUnreadFeedLoadedSkipsReloadWhenItemsAlreadyPresent() {
        let repository = FakeContentRepository()
        let viewModel = LongContentListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([makeSummary(id: 7, contentType: "article")])

        viewModel.ensureUnreadFeedLoaded()

        XCTAssertEqual(repository.loadPageCallCount, 0)
    }

    func testEnsureUnreadFeedLoadedRefreshesWhenListIsEmpty() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 7, contentType: "article")]
        )
        let viewModel = LongContentListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )

        viewModel.ensureUnreadFeedLoaded()

        XCTAssertEqual(repository.loadPageCallCount, 1)
        await assertEventuallyLoadedItems([7], in: viewModel)
    }

    func testRefreshUnreadFeedForcesReloadWhenItemsAlreadyPresent() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 9, contentType: "article")]
        )
        let viewModel = LongContentListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([makeSummary(id: 1, contentType: "article")])

        viewModel.refreshUnreadFeed()

        XCTAssertEqual(repository.loadPageCallCount, 1)
        await assertEventuallyLoadedItems([9], in: viewModel)
    }

    func testLoadNextPageIgnoresDuplicateTriggerWhilePageIsInFlight() {
        let repository = PendingContentRepository()
        let viewModel = LongContentListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )

        viewModel.loadNextPage()
        viewModel.loadNextPage()

        XCTAssertEqual(repository.loadPageCallCount, 1)
    }

    func testCancelledPageRequestDoesNotEnterErrorState() async {
        let repository = FailingContentRepository(error: URLError(.cancelled))
        let viewModel = LongContentListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )

        viewModel.refresh()

        await assertEventuallyState(.idle, in: viewModel)
        if case .error(let error) = viewModel.state {
            XCTFail("Expected cancellation to be ignored, got \(error.localizedDescription)")
        }
    }

    func testUnreadRefreshDoesNotReintroduceLocallyReadLongFormItem() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 11, contentType: "article")]
        )
        let viewModel = LongContentListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([makeSummary(id: 11, contentType: "article")])

        viewModel.markAsRead(11)
        viewModel.refreshUnreadFeed()

        await assertEventuallyLoadedItems([], in: viewModel)
    }

    func testShortNewsMarkAllVisibleAsReadKeepsItemsVisibleAndMarksNewsRowsRead() {
        let repository = FakeContentRepository()
        let readRepository = FakeReadStatusRepository()
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: readRepository,
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 11, contentType: "news"),
            makeSummary(id: 12, contentType: "news"),
        ])

        viewModel.markAllVisibleAsRead()

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
            makeSummary(id: 11, contentType: "news"),
            makeSummary(id: 12, contentType: "news"),
        ])

        viewModel.itemsScrolledPastTop(ids: [11])

        await assertEventuallyShortNewsItems(
            ids: [11, 12],
            readStates: [true, false],
            in: viewModel
        )
        XCTAssertEqual(readRepository.markReadCalls, [[11]])
    }

    func testShortNewsDetailReadNotificationKeepsItemVisibleAndMarksItRead() async {
        let repository = FakeContentRepository()
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 11, contentType: "news"),
            makeSummary(id: 12, contentType: "news"),
        ])

        NotificationCenter.default.post(
            name: .contentMarkedAsRead,
            object: nil,
            userInfo: ["contentId": 11, "contentType": "news"]
        )

        await assertEventuallyShortNewsItems(
            ids: [11, 12],
            readStates: [true, false],
            in: viewModel
        )
    }

    func testGroupedShortNewsMarkReadUsesNewsItemEndpoint() async {
        let readRepository = FakeReadStatusRepository()
        let viewModel = NewsGroupViewModel(
            repository: FakeContentRepository(),
            readRepository: readRepository,
            unreadCountService: .shared
        )
        viewModel.newsGroups = [
            NewsGroup(items: [
                makeSummary(id: 21, contentType: "news"),
                makeSummary(id: 22, contentType: "news"),
            ])
        ]

        await viewModel.markGroupAsRead("21")

        XCTAssertEqual(readRepository.markReadCalls, [[21, 22]])
        XCTAssertTrue(viewModel.newsGroups.first?.isRead ?? false)
    }

    func testGroupedShortNewsLoadUsesUnreadNewsFilter() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 31, contentType: "news")]
        )
        let viewModel = NewsGroupViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
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
                makeSummary(id: 3, contentType: "news"),
                makeSummary(id: 1, contentType: "news"),
                makeSummary(id: 2, contentType: "news"),
            ]
        )
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 2, contentType: "news"),
            makeSummary(id: 1, contentType: "news"),
        ])

        await viewModel.refreshInBackgroundAndWait()

        XCTAssertEqual(viewModel.currentItems().map(\.id), [3, 2, 1])
    }

    func testBackgroundRefreshDropsItemsMissingFromServerPage() async {
        let repository = FakeContentRepository(
            responseContents: [makeSummary(id: 1, contentType: "news")]
        )
        let viewModel = ShortNewsListViewModel(
            repository: repository,
            readRepository: FakeReadStatusRepository(),
            unreadCountService: .shared
        )
        viewModel.replaceItems([
            makeSummary(id: 1, contentType: "news"),
            makeSummary(id: 99, contentType: "news"),
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
        viewModel.updateReadFilter(.all)

        await fulfillment(of: [resumed], timeout: 2.0)
    }

    private func makeSummary(id: Int, contentType: String) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: "https://example.com/\(id)",
            title: "Item \(id)",
            source: "Example",
            platform: "Example",
            status: "completed",
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
        _ expectedState: LoadingState,
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
}

private final class PendingContentRepository: ContentRepositoryType {
    private(set) var loadPageCallCount = 0

    func loadPage(
        contentTypes: [APIContentType],
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) -> AnyPublisher<ContentListResponse, Error> {
        loadPageCallCount += 1
        return Empty<ContentListResponse, Error>(completeImmediately: false)
            .eraseToAnyPublisher()
    }

    func loadDetail(id: Int) -> AnyPublisher<ContentDetail, Error> {
        fatalError("unused in test")
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
    ) -> AnyPublisher<ContentListResponse, Error> {
        loadPageCallCount += 1
        return Fail(error: error).eraseToAnyPublisher()
    }

    func loadDetail(id: Int) -> AnyPublisher<ContentDetail, Error> {
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
    ) -> AnyPublisher<ContentListResponse, Error> {
        loadPageRequests.append(
            LoadPageRequest(
                contentTypes: contentTypes,
                readFilter: readFilter,
                cursor: cursor,
                limit: limit
            )
        )
        return Just(
            ContentListResponse(
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
        )
        .setFailureType(to: Error.self)
        .eraseToAnyPublisher()
    }

    func loadDetail(id: Int) -> AnyPublisher<ContentDetail, Error> {
        fatalError("unused in test")
    }
}

private final class FakeReadStatusRepository: ReadStatusRepositoryType {
    private(set) var markReadCalls: [[Int]] = []

    func markRead(ids: [Int]) -> AnyPublisher<Void, Error> {
        markReadCalls.append(ids)
        return Just(())
            .setFailureType(to: Error.self)
            .eraseToAnyPublisher()
    }
}
