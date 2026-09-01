import Foundation
import XCTest
@testable import newsly

@MainActor
final class SearchViewModelTests: XCTestCase {
    func testRapidTextReplacementCancelsAndFencesStaleLocalSuccess() async {
        let contentService = SearchContentServiceMock()
        let viewModel = SearchViewModel(
            contentService: contentService,
            scraperConfigService: SearchFeedServiceMock()
        )

        viewModel.contentResults = [makeContentSummary(id: 42)]
        viewModel.hasLocalSearch = true
        viewModel.searchText = "first topic"
        viewModel.searchTextDidChange(to: viewModel.searchText)

        XCTAssertTrue(viewModel.contentResults.isEmpty)
        XCTAssertFalse(viewModel.hasLocalSearch)
        XCTAssertTrue(viewModel.isLoadingLocal)

        let firstRequestStarted = await waitUntil {
            contentService.requestedLocalQueries == ["first topic"]
        }
        XCTAssertTrue(firstRequestStarted)

        viewModel.searchText = "current topic"
        viewModel.searchTextDidChange(to: viewModel.searchText)
        let currentRequestStarted = await waitUntil {
            contentService.requestedLocalQueries == ["first topic", "current topic"]
        }
        XCTAssertTrue(currentRequestStarted)

        contentService.resumeLocalSearch(
            query: "current topic",
            with: .success(makeContentListResponse(ids: [2]))
        )
        let currentResultsLoaded = await waitUntil {
            viewModel.contentResults.map(\.id) == [2] && !viewModel.isLoadingLocal
        }
        XCTAssertTrue(currentResultsLoaded)

        contentService.resumeLocalSearch(
            query: "first topic",
            with: .success(makeContentListResponse(ids: [1]))
        )
        let staleRequestObservedCancellation = await waitUntil {
            contentService.cancelledLocalQueries.contains("first topic")
        }
        XCTAssertTrue(staleRequestObservedCancellation)
        XCTAssertEqual(viewModel.contentResults.map(\.id), [2])
        XCTAssertNil(viewModel.localErrorMessage)
        XCTAssertFalse(viewModel.isLoadingLocal)
    }

    func testRetryReplacesTrackedLocalSearchAndFencesStaleFailure() async {
        let contentService = SearchContentServiceMock()
        let viewModel = SearchViewModel(
            contentService: contentService,
            scraperConfigService: SearchFeedServiceMock()
        )
        viewModel.searchText = "retry topic"

        viewModel.retrySearch()
        let firstRequestStarted = await waitUntil {
            contentService.requestedLocalQueries == ["retry topic"]
        }
        XCTAssertTrue(firstRequestStarted)

        viewModel.retrySearch()
        let retryRequestStarted = await waitUntil {
            contentService.requestedLocalQueries == ["retry topic", "retry topic"]
        }
        XCTAssertTrue(retryRequestStarted)

        contentService.resumeLocalSearch(
            query: "retry topic",
            occurrence: 1,
            with: .success(makeContentListResponse(ids: [7]))
        )
        let retryResultsLoaded = await waitUntil {
            viewModel.contentResults.map(\.id) == [7] && !viewModel.isLoadingLocal
        }
        XCTAssertTrue(retryResultsLoaded)

        contentService.resumeLocalSearch(
            query: "retry topic",
            with: .failure(SearchTestError.failed)
        )
        let staleRequestObservedCancellation = await waitUntil {
            contentService.cancelledLocalQueries.contains("retry topic")
        }
        XCTAssertTrue(staleRequestObservedCancellation)
        XCTAssertEqual(viewModel.contentResults.map(\.id), [7])
        XCTAssertNil(viewModel.localErrorMessage)
        XCTAssertTrue(viewModel.hasLocalSearch)
        XCTAssertFalse(viewModel.isLoadingLocal)
    }

    func testQueryBelowTwoCharactersCancelsLocalWorkAndClearsLoading() async {
        let contentService = SearchContentServiceMock()
        let viewModel = SearchViewModel(
            contentService: contentService,
            scraperConfigService: SearchFeedServiceMock()
        )
        viewModel.searchText = "valid topic"
        viewModel.retrySearch()
        let requestStarted = await waitUntil {
            contentService.requestedLocalQueries == ["valid topic"]
        }
        XCTAssertTrue(requestStarted)
        XCTAssertTrue(viewModel.isLoadingLocal)

        viewModel.searchText = "x"
        viewModel.searchTextDidChange(to: viewModel.searchText)

        XCTAssertFalse(viewModel.isLoadingLocal)
        XCTAssertFalse(viewModel.hasLocalSearch)
        XCTAssertTrue(viewModel.contentResults.isEmpty)
        XCTAssertNil(viewModel.localErrorMessage)

        contentService.resumeLocalSearch(
            query: "valid topic",
            with: .success(makeContentListResponse(ids: [9]))
        )
        let staleRequestObservedCancellation = await waitUntil {
            contentService.cancelledLocalQueries.contains("valid topic")
        }
        XCTAssertTrue(staleRequestObservedCancellation)
        XCTAssertFalse(viewModel.isLoadingLocal)
        XCTAssertFalse(viewModel.hasLocalSearch)
        XCTAssertTrue(viewModel.contentResults.isEmpty)
        XCTAssertNil(viewModel.localErrorMessage)
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
        XCTAssertEqual(viewModel.mixedErrorMessage, "Newsbuddy couldn't search external sources.")
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
    private var localContinuations: [
        String: [CheckedContinuation<Result<ContentListResponse, Error>, Never>]
    ] = [:]
    private var mixedContinuations: [String: CheckedContinuation<MixedSearchResponse, Error>] = [:]
    private(set) var requestedLocalQueries: [String] = []
    private(set) var cancelledLocalQueries: [String] = []
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
        requestedLocalQueries.append(query)
        let result = await withCheckedContinuation { continuation in
            localContinuations[query, default: []].append(continuation)
        }
        if Task.isCancelled {
            cancelledLocalQueries.append(query)
        }
        return try result.get()
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

    func resumeLocalSearch(
        query: String,
        occurrence: Int = 0,
        with result: Result<ContentListResponse, Error>
    ) {
        guard var continuations = localContinuations[query],
              continuations.indices.contains(occurrence) else {
            XCTFail("No pending local search for \(query) at occurrence \(occurrence)")
            return
        }
        let continuation = continuations.remove(at: occurrence)
        if continuations.isEmpty {
            localContinuations.removeValue(forKey: query)
        } else {
            localContinuations[query] = continuations
        }
        continuation.resume(returning: result)
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
            limit: nil,
            isActive: true,
            createdAt: Date(),
            stats: nil,
            subscriptionOutcome: subscriptionOutcome,
            backfillTaskId: nil
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
          "description": null,
          "rationale": null,
          "evidence_url": null,
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

private func makeContentListResponse(ids: [Int]) -> ContentListResponse {
    ContentListResponse(
        contents: ids.map(makeContentSummary),
        availableDates: [],
        contentTypes: [],
        meta: PaginationMetadata(
            nextCursor: nil,
            pageSize: ids.count,
            total: nil
        )
    )
}
