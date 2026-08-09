import Foundation
import XCTest
@testable import newsly

@MainActor
final class SearchViewModelTests: XCTestCase {
    func testChangingValidQueryImmediatelyInvalidatesPreviousLocalResults() async {
        let viewModel = SearchViewModel(
            contentService: SearchContentServiceMock(),
            scraperConfigService: SearchFeedServiceMock()
        )
        viewModel.searchText = "previous topic"
        viewModel.contentResults = [makeContentSummary(id: 42)]
        viewModel.hasLocalSearch = true

        viewModel.searchText = "current topic"
        let searchTask = Task {
            await viewModel.handleSearchTextChangedAfterDelay()
        }
        await Task.yield()

        XCTAssertTrue(viewModel.contentResults.isEmpty)
        XCTAssertFalse(viewModel.hasLocalSearch)

        searchTask.cancel()
        await searchTask.value
    }

    func testSupersededMixedSearchCannotReplaceCurrentResults() async {
        let contentService = SearchContentServiceMock()
        let viewModel = SearchViewModel(
            contentService: contentService,
            scraperConfigService: SearchFeedServiceMock()
        )

        viewModel.searchText = "first topic"
        viewModel.submitSearch()
        let firstRequestStarted = await waitUntil {
            contentService.requestedMixedQueries == ["first topic"]
        }
        XCTAssertTrue(firstRequestStarted)

        viewModel.searchText = "current topic"
        viewModel.submitSearch()
        let currentRequestStarted = await waitUntil {
            contentService.requestedMixedQueries == ["first topic", "current topic"]
        }
        XCTAssertTrue(currentRequestStarted)

        contentService.resumeMixedSearch(
            query: "current topic",
            with: .success(makeMixedResponse(query: "current topic", feedID: "current-feed"))
        )
        let currentResultsLoaded = await waitUntil {
            viewModel.feedResults.first?.id == "current-feed"
        }
        XCTAssertTrue(currentResultsLoaded)

        contentService.resumeMixedSearch(
            query: "first topic",
            with: .success(makeMixedResponse(query: "first topic", feedID: "stale-feed"))
        )
        await Task.yield()

        XCTAssertEqual(viewModel.feedResults.map(\.id), ["current-feed"])
        XCTAssertFalse(viewModel.isLoadingMixed)
        XCTAssertTrue(viewModel.hasSubmittedSearch)
    }

    func testExternalSearchFailureKeepsLocalResultsVisible() async {
        let contentService = SearchContentServiceMock(
            immediateMixedResults: [.failure(SearchTestError.failed)]
        )
        let viewModel = SearchViewModel(
            contentService: contentService,
            scraperConfigService: SearchFeedServiceMock()
        )
        viewModel.searchText = "local topic"
        viewModel.contentResults = [makeContentSummary(id: 42)]
        viewModel.hasLocalSearch = true

        viewModel.submitSearch()
        let searchCompleted = await waitUntil { viewModel.hasSubmittedSearch }
        XCTAssertTrue(searchCompleted)

        XCTAssertEqual(viewModel.contentResults.map(\.id), [42])
        XCTAssertEqual(viewModel.mixedErrorMessage, "Newsly couldn't search external sources.")
        XCTAssertTrue(viewModel.feedResults.isEmpty)
        XCTAssertTrue(viewModel.podcastResults.isEmpty)
    }

    func testFeedSubscriptionFailureIsScopedToThatResult() async throws {
        let feedService = SearchFeedServiceMock(error: SearchTestError.failed)
        let viewModel = SearchViewModel(
            contentService: SearchContentServiceMock(),
            scraperConfigService: feedService
        )
        let response = makeMixedResponse(query: "topic", feedID: "failed-feed")
        let result = try XCTUnwrap(response.feeds.first)

        await viewModel.subscribeToFeed(result)

        XCTAssertEqual(
            viewModel.actionErrorMessages["feed:failed-feed"],
            "That action didn't finish. Please try again."
        )
        XCTAssertNil(viewModel.completedActionLabels["feed:failed-feed"])
        XCTAssertTrue(viewModel.actionInFlightIds.isEmpty)
    }

    func testDuplicateFeedSubscriptionReportsTruthfulOutcome() async throws {
        let viewModel = SearchViewModel(
            contentService: SearchContentServiceMock(),
            scraperConfigService: SearchFeedServiceMock(subscriptionOutcome: .already_subscribed)
        )
        let result = try XCTUnwrap(
            makeMixedResponse(query: "topic", feedID: "existing-feed").feeds.first
        )

        await viewModel.subscribeToFeed(result)

        XCTAssertEqual(
            viewModel.completedActionLabels["feed:existing-feed"],
            "Already subscribed"
        )
    }

    func testMixedSearchPreMarksPersistedFeedSubscription() async {
        let response = makeMixedResponse(
            query: "topic",
            feedID: "existing-feed",
            isSubscribed: true
        )
        let feedService = SearchFeedServiceMock()
        let viewModel = SearchViewModel(
            contentService: SearchContentServiceMock(
                immediateMixedResults: [.success(response)]
            ),
            scraperConfigService: feedService
        )
        viewModel.searchText = "topic"

        viewModel.submitSearch()
        let searchCompleted = await waitUntil { viewModel.hasSubmittedSearch }
        XCTAssertTrue(searchCompleted)
        XCTAssertEqual(viewModel.completedActionLabels["feed:existing-feed"], "Subscribed")

        if let result = viewModel.feedResults.first {
            await viewModel.subscribeToFeed(result)
        }
        XCTAssertEqual(feedService.subscribeCallCount, 0)
    }

    func testRepeatedPodcastEpisodeAddRunsOnce() async throws {
        let contentService = SearchContentServiceMock(
            submitResult: .success(Self.submitResponse())
        )
        let viewModel = SearchViewModel(
            contentService: contentService,
            scraperConfigService: SearchFeedServiceMock()
        )
        let result = try Self.podcastResult()

        await viewModel.addPodcastEpisode(result)
        await viewModel.addPodcastEpisode(result)

        XCTAssertEqual(contentService.submitCallCount, 1)
        XCTAssertEqual(viewModel.completedActionLabels["episode:\(result.id)"], "Added")
    }

    func testRepeatedPodcastSubscriptionRunsOnce() async throws {
        let feedService = SearchFeedServiceMock()
        let viewModel = SearchViewModel(
            contentService: SearchContentServiceMock(),
            scraperConfigService: feedService
        )
        let result = try Self.podcastResult()

        await viewModel.subscribeToPodcast(result)
        await viewModel.subscribeToPodcast(result)

        XCTAssertEqual(feedService.subscribeCallCount, 1)
        XCTAssertEqual(
            viewModel.completedActionLabels["podcast-feed:https://example.com/feed.xml"],
            "Subscribed"
        )
    }

    private func waitUntil(
        timeoutNanoseconds: UInt64 = 1_000_000_000,
        condition: @escaping @MainActor () -> Bool
    ) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .nanoseconds(Int64(timeoutNanoseconds)))
        while ContinuousClock.now < deadline {
            if condition() { return true }
            await Task.yield()
        }
        return condition()
    }

    nonisolated private static func submitResponse() -> SubmitContentResponse {
        SubmitContentResponse(
            contentId: 42,
            contentType: .podcast,
            status: .new,
            platform: nil,
            alreadyExists: false,
            message: "Queued",
            taskId: 99,
            source: "self submission"
        )
    }

    nonisolated private static func podcastResult() throws -> PodcastSearchResult {
        let json = """
        {
          "title": "Episode",
          "episode_url": "https://example.com/episode",
          "podcast_title": "Example Podcast",
          "feed_url": "https://example.com/feed.xml"
        }
        """
        return try JSONDecoder().decode(PodcastSearchResult.self, from: Data(json.utf8))
    }
}

@MainActor
private final class SearchContentServiceMock: SearchContentServicing {
    private var immediateMixedResults: [Result<MixedSearchResponse, Error>]
    private let submitResult: Result<SubmitContentResponse, Error>?
    private var mixedContinuations: [String: CheckedContinuation<MixedSearchResponse, Error>] = [:]
    private(set) var requestedMixedQueries: [String] = []
    private(set) var submitCallCount = 0

    init(
        immediateMixedResults: [Result<MixedSearchResponse, Error>] = [],
        submitResult: Result<SubmitContentResponse, Error>? = nil
    ) {
        self.immediateMixedResults = immediateMixedResults
        self.submitResult = submitResult
    }

    func searchContent(
        query: String,
        contentType: String,
        limit: Int,
        cursor: String?
    ) async throws -> ContentListResponse {
        ContentListResponse(
            contents: [],
            availableDates: [],
            contentTypes: [],
            meta: PaginationMetadata(pageSize: limit)
        )
    }

    func searchMixed(query: String, limit: Int) async throws -> MixedSearchResponse {
        requestedMixedQueries.append(query)
        if !immediateMixedResults.isEmpty {
            return try immediateMixedResults.removeFirst().get()
        }
        return try await withCheckedThrowingContinuation { continuation in
            mixedContinuations[query] = continuation
        }
    }

    func submitContent(
        url: URL,
        contentType: String?,
        title: String?,
        platform: String?
    ) async throws -> SubmitContentResponse {
        submitCallCount += 1
        guard let submitResult else { throw SearchTestError.failed }
        return try submitResult.get()
    }

    func resumeMixedSearch(query: String, with result: Result<MixedSearchResponse, Error>) {
        guard let continuation = mixedContinuations.removeValue(forKey: query) else {
            XCTFail("No pending mixed search for \(query)")
            return
        }
        continuation.resume(with: result)
    }
}

@MainActor
private final class SearchFeedServiceMock: SearchFeedSubscribing {
    private let error: Error?
    private let subscriptionOutcome: APIFeedSubscriptionOutcome?
    private(set) var subscribeCallCount = 0

    init(
        error: Error? = nil,
        subscriptionOutcome: APIFeedSubscriptionOutcome? = nil
    ) {
        self.error = error
        self.subscriptionOutcome = subscriptionOutcome
    }

    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig {
        subscribeCallCount += 1
        if let error { throw error }
        return ScraperConfig(
            id: 1,
            scraperType: feedType,
            displayName: displayName,
            config: ["feed_url": AnyCodable(feedURL)],
            feedUrl: feedURL,
            isActive: true,
            createdAt: Date(),
            subscriptionOutcome: subscriptionOutcome
        )
    }
}

private enum SearchTestError: Error {
    case failed
}

private func makeMixedResponse(
    query: String,
    feedID: String,
    isSubscribed: Bool = false
) -> MixedSearchResponse {
    let json = """
    {
      "query": "\(query)",
      "content": [],
      "feeds": [
        {
          "id": "\(feedID)",
          "title": "Example feed",
          "site_url": "https://example.com",
          "feed_url": "https://example.com/feed.xml",
          "feed_type": "rss",
          "feed_format": "rss",
          "is_subscribed": \(isSubscribed)
        }
      ],
      "podcasts": []
    }
    """
    return try! JSONDecoder().decode(MixedSearchResponse.self, from: Data(json.utf8))
}

private func makeContentSummary(id: Int) -> ContentSummary {
    ContentSummary(
        id: id,
        contentType: .article,
        url: "https://example.com/\(id)",
        title: "Local result",
        source: "Example",
        platform: "web",
        status: .completed,
        shortSummary: "Summary",
        createdAt: "2026-08-07T12:00:00Z",
        processedAt: "2026-08-07T12:01:00Z",
        classification: nil,
        publicationDate: nil,
        isRead: false,
        isSavedToKnowledge: false
    )
}
