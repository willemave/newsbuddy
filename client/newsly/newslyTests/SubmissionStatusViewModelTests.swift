import Foundation
import XCTest
@testable import newsly

@MainActor
final class SubmissionStatusViewModelTests: XCTestCase {
    func testCanonicalFeedResultOverridesLegacyCompatibilityFields() {
        let feed = APIDetectedFeed(
            url: "https://example.com/feed.xml",
            type: "rss",
            title: "Canonical Feed"
        )
        let subscription = APISubmissionFeedSubscriptionResponse(
            status: "created",
            feedUrl: feed.url,
            feedType: feed.type,
            created: true,
            configId: nil,
            initialDownload: nil
        )
        let response = APISubmissionStatusResponse(
            id: 43,
            contentType: .unknown,
            url: "https://example.com",
            sourceUrl: nil,
            title: nil,
            status: .completed,
            errorMessage: nil,
            createdAt: Date(timeIntervalSince1970: 0),
            processedAt: nil,
            submittedVia: nil,
            result: .feed_subscription(
                APISubmissionFeedSubscriptionResult(
                    outcome: .subscribed,
                    detectedFeed: feed,
                    subscription: subscription
                )
            ),
            submissionKind: .content,
            outcome: .failed,
            rationale: "stale compatibility value",
            detectedFeed: nil,
            feedSubscription: nil
        )

        let item = SubmissionStatusItem(api: response)

        XCTAssertTrue(item.isFeedSubscription)
        XCTAssertEqual(item.outcome, .subscribed)
        XCTAssertEqual(item.detectedFeed?.title, "Canonical Feed")
        XCTAssertEqual(item.feedSubscription?.status, "created")
        XCTAssertNil(item.rationale)
    }

    func testUnknownCanonicalResultUsesTemporaryCompatibilityFields() {
        let response = APISubmissionStatusResponse(
            id: 44,
            contentType: .unknown,
            url: "https://example.com/future",
            sourceUrl: nil,
            title: nil,
            status: .processing,
            errorMessage: nil,
            createdAt: Date(timeIntervalSince1970: 0),
            processedAt: nil,
            submittedVia: nil,
            result: .unknown("future_result", AnyCodable(["value": true])),
            submissionKind: .learning_deck,
            outcome: .queued,
            rationale: "Compatibility projection",
            detectedFeed: nil,
            feedSubscription: nil
        )

        let item = SubmissionStatusItem(api: response)

        XCTAssertTrue(item.isLearningDeck)
        XCTAssertEqual(item.outcome, .queued)
        XCTAssertEqual(item.rationale, "Compatibility projection")
    }

    func testFeedSubscriptionSuccessDisplaysSemanticStatus() {
        let item = SubmissionStatusItem(
            id: 44,
            contentType: .unknown,
            url: "https://chinai.substack.com/p/example",
            sourceUrl: nil,
            title: nil,
            status: .skipped,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_sheet",
            isSelfSubmission: true,
            submissionKind: .feed_subscription,
            outcome: .subscribed,
            detectedFeed: DetectedFeed(
                url: "https://chinai.substack.com/feed",
                type: "substack",
                title: "ChinAI Newsletter",
                format: "rss"
            ),
            feedSubscription: SubmissionFeedSubscription(
                status: "created",
                feedUrl: "https://chinai.substack.com/feed",
                feedType: "substack",
                created: true,
                configId: 44,
                initialDownload: SubmissionFeedInitialDownload(
                    requestedCount: 2,
                    ran: true,
                    status: "completed",
                    saved: 3,
                    duplicates: 0,
                    errors: 0
                )
            )
        )

        XCTAssertEqual(item.displayTitle, "ChinAI Newsletter")
        XCTAssertEqual(item.statusLabel, "Subscribed")
        XCTAssertFalse(item.isError)
        XCTAssertEqual(item.statusDetailText, "Feed added; 3 recent items saved.")
    }

    func testGenericSkippedSubmissionRemainsError() {
        let item = makeSubmission(
            id: 99,
            createdAt: "2026-05-26T22:09:04Z",
            outcome: .skipped
        )

        XCTAssertEqual(item.statusLabel, "Skipped")
        XCTAssertTrue(item.isError)
        XCTAssertEqual(item.errorDisplayText, "Processing was skipped.")
    }

    func testFeedDetectionFailureUsesActionableStatus() {
        let item = SubmissionStatusItem(
            id: 45,
            contentType: .unknown,
            url: "https://example.com/no-feed",
            sourceUrl: nil,
            title: nil,
            status: .skipped,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_sheet",
            isSelfSubmission: true,
            submissionKind: .feed_subscription,
            outcome: .feed_not_found,
            detectedFeed: nil,
            feedSubscription: SubmissionFeedSubscription(status: "no_feed_found")
        )

        XCTAssertEqual(item.statusLabel, "Feed not found")
        XCTAssertTrue(item.isError)
        XCTAssertEqual(item.errorDisplayText, "No RSS or Atom feed was found for this URL.")
    }

    func testGenericFailureDoesNotExposeBackendErrorText() {
        let item = SubmissionStatusItem(
            id: 46,
            contentType: .article,
            url: "https://example.com/article",
            sourceUrl: nil,
            title: "Example article",
            status: .failed,
            errorMessage: "sqlalchemy.exc.OperationalError: internal-host:5432",
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_sheet",
            isSelfSubmission: true,
            outcome: .failed
        )

        XCTAssertEqual(
            item.errorDisplayText,
            "Newsly couldn't finish processing this item. Try submitting it again."
        )
        XCTAssertFalse(item.errorDisplayText?.contains("sqlalchemy") == true)
        XCTAssertFalse(item.errorDisplayText?.contains("internal-host") == true)
        XCTAssertEqual(item.recoveryURL?.absoluteString, item.url)
    }

    func testNoActionShowsRationaleAndOffersShareSheetRecovery() {
        let item = SubmissionStatusItem(
            id: 47,
            contentType: .unknown,
            url: "https://example.com/unsupported-homepage",
            sourceUrl: nil,
            title: nil,
            status: .completed,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_action",
            isSelfSubmission: true,
            outcome: .no_action,
            rationale: "Neither a continuing source nor an eligible item was found."
        )

        XCTAssertEqual(item.statusLabel, "No action taken")
        XCTAssertFalse(item.isError)
        XCTAssertEqual(
            item.statusDetailText,
            "Neither a continuing source nor an eligible item was found."
        )
        XCTAssertEqual(item.recoveryURL?.absoluteString, item.url)
    }

    func testFailedShareActionOffersShareSheetRecovery() {
        let item = SubmissionStatusItem(
            id: 48,
            contentType: .article,
            url: "https://example.com/failed-share",
            sourceUrl: nil,
            title: "Failed share",
            status: .failed,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_action",
            isSelfSubmission: true,
            outcome: .failed
        )

        XCTAssertEqual(item.recoveryURL?.absoluteString, item.url)
    }

    func testFeedFailureFromShareActionOffersShareSheetRecovery() {
        let item = SubmissionStatusItem(
            id: 49,
            contentType: .unknown,
            url: "https://example.com/feed-page",
            sourceUrl: nil,
            title: nil,
            status: .failed,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_action",
            isSelfSubmission: true,
            submissionKind: .feed_subscription,
            outcome: .feed_fetch_failed
        )

        XCTAssertEqual(item.recoveryURL?.absoluteString, item.url)
    }

    func testLegacyInstructionShareFailureOffersShareSheetRecovery() {
        let item = SubmissionStatusItem(
            id: 50,
            contentType: .article,
            url: "https://example.com/instruction-share",
            sourceUrl: nil,
            title: nil,
            status: .failed,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: nil,
            submittedVia: "share_sheet_instruction",
            isSelfSubmission: true,
            outcome: .failed
        )

        XCTAssertEqual(item.recoveryURL?.absoluteString, item.url)
    }

    func testFailedNonShareAndUnsafeShareURLsDoNotExposeRecovery() {
        let nonShare = SubmissionStatusItem(
            id: 51,
            contentType: .article,
            url: "https://example.com/non-share",
            sourceUrl: nil,
            title: nil,
            status: .failed,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: nil,
            submittedVia: "assistant",
            isSelfSubmission: false,
            outcome: .failed
        )
        let unsafeShare = SubmissionStatusItem(
            id: 52,
            contentType: .unknown,
            url: "javascript:alert(1)",
            sourceUrl: nil,
            title: nil,
            status: .failed,
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: nil,
            submittedVia: "share_action",
            isSelfSubmission: true,
            outcome: .failed
        )

        XCTAssertNil(nonShare.recoveryURL)
        XCTAssertNil(unsafeShare.recoveryURL)
    }

    func testUnseenCountDefaultsToAllLoadedSubmissions() {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }

        let viewModel = makeViewModel(defaults: defaults)
        viewModel.submissions = [
            makeSubmission(id: 1, createdAt: "2026-04-10T10:00:00Z"),
            makeSubmission(id: 2, createdAt: "2026-04-10T09:00:00Z")
        ]

        XCTAssertEqual(viewModel.unseenCount, 2)
    }

    func testLoadStoresSubmissionsAndPagination() async {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }

        var requestedCursors: [String?] = []
        let viewModel = makeViewModel(defaults: defaults) { cursor in
            requestedCursors.append(cursor)
            return self.makeFeed(
                submissions: [
                    self.makeSubmission(id: 1, createdAt: "2026-04-10T10:00:00Z"),
                    self.makeSubmission(id: 2, createdAt: "2026-04-10T09:00:00Z")
                ],
                nextCursor: "next",
                hasMore: true
            )
        }

        await viewModel.load()

        XCTAssertEqual(requestedCursors.count, 1)
        XCTAssertNil(requestedCursors[0])
        XCTAssertEqual(viewModel.submissions.map(\.id), [1, 2])
        XCTAssertEqual(viewModel.nextCursor, "next")
        XCTAssertTrue(viewModel.hasMore)
        XCTAssertFalse(viewModel.isLoading)
        XCTAssertNil(viewModel.errorMessage)
    }

    func testLoadMoreAppendsSubmissionsThroughPaginationFeed() async {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }

        var requestedCursors: [String?] = []
        var feeds = [
            makeFeed(
                submissions: [makeSubmission(id: 1, createdAt: "2026-04-10T10:00:00Z")],
                nextCursor: "next",
                hasMore: true
            ),
            makeFeed(
                submissions: [makeSubmission(id: 2, createdAt: "2026-04-10T09:00:00Z")],
                nextCursor: nil,
                hasMore: false
            ),
        ]
        let viewModel = makeViewModel(defaults: defaults) { cursor in
            requestedCursors.append(cursor)
            return feeds.removeFirst()
        }

        await viewModel.load()
        await viewModel.loadMore()

        XCTAssertEqual(requestedCursors.count, 2)
        XCTAssertNil(requestedCursors[0])
        XCTAssertEqual(requestedCursors[1], "next")
        XCTAssertEqual(viewModel.submissions.map(\.id), [1, 2])
        XCTAssertNil(viewModel.nextCursor)
        XCTAssertFalse(viewModel.hasMore)
        XCTAssertFalse(viewModel.isLoadingMore)
    }

    func testMarkCurrentSubmissionsViewedClearsCurrentBadgeAndPersists() {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }

        let viewModel = makeViewModel(defaults: defaults)
        viewModel.submissions = [
            makeSubmission(id: 1, createdAt: "2026-04-10T10:00:00Z"),
            makeSubmission(id: 2, createdAt: "2026-04-10T09:00:00Z")
        ]

        viewModel.markCurrentSubmissionsViewed()

        XCTAssertEqual(viewModel.unseenCount, 0)

        let reloadedViewModel = makeViewModel(defaults: defaults)
        reloadedViewModel.submissions = [
            makeSubmission(id: 1, createdAt: "2026-04-10T10:00:00Z"),
            makeSubmission(id: 2, createdAt: "2026-04-10T09:00:00Z"),
            makeSubmission(id: 3, createdAt: "2026-04-10T10:30:00Z")
        ]

        XCTAssertEqual(reloadedViewModel.unseenCount, 1)
    }

    private func makeSubmission(
        id: Int,
        createdAt: String,
        outcome: APISubmissionOutcome = .processing
    ) -> SubmissionStatusItem {
        SubmissionStatusItem(
            id: id,
            contentType: .article,
            url: "https://example.com/\(id)",
            sourceUrl: nil,
            title: "Submission \(id)",
            status: .processing,
            errorMessage: nil,
            createdAt: createdAt,
            processedAt: nil,
            submittedVia: "app",
            isSelfSubmission: true,
            outcome: outcome
        )
    }

    private func makeViewModel(
        defaults: UserDefaults,
        loadPage: ((_ cursor: String?) async throws -> SubmissionStatusFeed)? = nil
    ) -> SubmissionStatusViewModel {
        let resolvedLoadPage = loadPage ?? { _ in
            self.makeFeed(submissions: [], nextCursor: nil, hasMore: false)
        }
        return SubmissionStatusViewModel(defaults: defaults, loadPage: resolvedLoadPage)
    }

    private func makeFeed(
        submissions: [SubmissionStatusItem],
        nextCursor: String?,
        hasMore: Bool
    ) -> SubmissionStatusFeed {
        SubmissionStatusFeed(
            submissions: submissions,
            meta: PaginationMetadata(
                nextCursor: nextCursor,
                hasMore: hasMore,
                pageSize: submissions.count,
                total: submissions.count
            )
        )
    }

    private func makeIsolatedDefaults(
        file: StaticString = #filePath,
        line: UInt = #line
    ) -> (defaults: UserDefaults, suiteName: String) {
        let suiteName = "SubmissionStatusViewModelTests.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suiteName) else {
            XCTFail("Failed to create isolated UserDefaults", file: file, line: line)
            fatalError("Failed to create isolated UserDefaults")
        }
        defaults.removePersistentDomain(forName: suiteName)
        return (defaults, suiteName)
    }

    private func clear(_ suiteName: String, defaults: UserDefaults) {
        defaults.removePersistentDomain(forName: suiteName)
    }
}
