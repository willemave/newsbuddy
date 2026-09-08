import XCTest
@testable import newsly

@MainActor
final class FeedHistoryViewModelTests: XCTestCase {
    func testFilterChangeDiscardsEarlierResponse() async {
        let requested = expectation(description: "First history request started")
        var earlierResponse: CheckedContinuation<APIFeedHistoryResponse, Error>?
        let service = HistoryServiceStub { status, _ in
            if status == "completed" {
                return try await withCheckedThrowingContinuation { continuation in
                    earlierResponse = continuation
                    requested.fulfill()
                }
            }
            return APIFeedHistoryResponse(items: status == "failed" ? [Self.item(2)] : [], nextOffset: nil)
        }
        let model = FeedHistoryViewModel(configId: 1, service: service)
        let oldRequest = Task { await model.refresh(status: "completed") }
        await fulfillment(of: [requested], timeout: 2)
        await model.refresh(status: "failed")
        earlierResponse?.resume(returning: APIFeedHistoryResponse(items: [Self.item(1)], nextOffset: 30))
        await oldRequest.value
        XCTAssertEqual(model.items.map(\.id), [2])
        XCTAssertNil(model.nextOffset)
        XCTAssertFalse(model.isLoading)
    }

    func testPaginationDeduplicatesAndKeepsPageAfterFailure() async {
        var failNextPage = true
        let service = HistoryServiceStub { status, offset in
            if status == "active" { return APIFeedHistoryResponse(items: [], nextOffset: nil) }
            if offset == 0 { return APIFeedHistoryResponse(items: [Self.item(1)], nextOffset: 30) }
            if failNextPage { throw URLError(.networkConnectionLost) }
            return APIFeedHistoryResponse(items: [Self.item(1), Self.item(2)], nextOffset: nil)
        }
        let model = FeedHistoryViewModel(configId: 1, service: service)
        await model.refresh(status: "completed")
        await model.loadMore()
        XCTAssertEqual(model.items.map(\.id), [1])
        XCTAssertEqual(model.nextOffset, 30)
        XCTAssertNotNil(model.errorMessage)
        failNextPage = false
        await model.loadMore()
        XCTAssertEqual(model.items.map(\.id), [1, 2])
        XCTAssertNil(model.errorMessage)
    }

    func testActivityRefreshPreservesLoadedHistory() async {
        let service = HistoryServiceStub { status, _ in
            APIFeedHistoryResponse(items: [Self.item(status == "active" ? 2 : 1)], nextOffset: 30)
        }
        let model = FeedHistoryViewModel(configId: 1, service: service)
        await model.refresh(status: "completed")
        await model.refreshActivity()
        XCTAssertEqual(model.items.map(\.id), [1])
        XCTAssertEqual(model.activeItems.map(\.id), [2])
        XCTAssertEqual(model.nextOffset, 30)
    }

    func testCurrentWorkRefreshRemovesFinishedItems() async {
        var finished = false
        let service = HistoryServiceStub { _, _ in
            APIFeedHistoryResponse(items: finished ? [] : [Self.item(1)], nextOffset: nil)
        }
        let model = FeedHistoryViewModel(configId: 1, service: service)
        await model.refresh(status: "active")
        finished = true
        await model.refreshActivity()
        XCTAssertTrue(model.items.isEmpty)
        XCTAssertTrue(model.activeItems.isEmpty)
    }

    private static func item(_ id: Int) -> APIFeedHistoryItem {
        APIFeedHistoryItem(id: id, title: "Item \(id)", contentType: "article", status: "completed",
                           stage: nil, processedAt: nil, publicationAt: nil, durationSeconds: nil, readingMinutes: nil)
    }
}

private final class HistoryServiceStub: FeedHistoryServicing {
    let handler: (String, Int) async throws -> APIFeedHistoryResponse

    init(handler: @escaping (String, Int) async throws -> APIFeedHistoryResponse) {
        self.handler = handler
    }

    func feedHistory(configId: Int, status: String, offset: Int) async throws -> APIFeedHistoryResponse {
        try await handler(status, offset)
    }
}
