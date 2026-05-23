import Combine
import XCTest
@testable import newsly

@MainActor
final class TabCoordinatorViewModelTests: XCTestCase {
    func testHandleTabChangeDoesNotResetOutgoingShortNewsState() {
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
        contentTypes: [ContentType],
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
        contentTypes: [ContentType],
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
    private let responseContents: [ContentSummary]
    private(set) var loadPageCallCount = 0

    init(responseContents: [ContentSummary] = []) {
        self.responseContents = responseContents
    }

    func loadPage(
        contentTypes: [ContentType],
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) -> AnyPublisher<ContentListResponse, Error> {
        loadPageCallCount += 1
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
    func markRead(ids: [Int]) -> AnyPublisher<Void, Error> {
        Just(())
            .setFailureType(to: Error.self)
            .eraseToAnyPublisher()
    }
}
