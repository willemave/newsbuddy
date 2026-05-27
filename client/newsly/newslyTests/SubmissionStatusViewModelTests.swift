import Foundation
import XCTest
@testable import newsly

@MainActor
final class SubmissionStatusViewModelTests: XCTestCase {
    func testFeedSubscriptionSuccessDisplaysSemanticStatus() {
        let item = SubmissionStatusItem(
            id: 44,
            contentType: "unknown",
            url: "https://chinai.substack.com/p/example",
            sourceUrl: nil,
            title: nil,
            status: "skipped",
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_sheet",
            isSelfSubmission: true,
            submissionKind: "feed_subscription",
            outcome: "subscribed",
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
            status: "skipped"
        )

        XCTAssertEqual(item.statusLabel, "Skipped")
        XCTAssertTrue(item.isError)
        XCTAssertEqual(item.errorDisplayText, "Processing was skipped.")
    }

    func testFeedDetectionFailureUsesActionableStatus() {
        let item = SubmissionStatusItem(
            id: 45,
            contentType: "unknown",
            url: "https://example.com/no-feed",
            sourceUrl: nil,
            title: nil,
            status: "skipped",
            errorMessage: nil,
            createdAt: "2026-05-26T22:09:04Z",
            processedAt: "2026-05-26T22:09:10Z",
            submittedVia: "share_sheet",
            isSelfSubmission: true,
            submissionKind: "feed_subscription",
            outcome: "feed_not_found",
            detectedFeed: nil,
            feedSubscription: SubmissionFeedSubscription(status: "no_feed_found")
        )

        XCTAssertEqual(item.statusLabel, "Feed not found")
        XCTAssertTrue(item.isError)
        XCTAssertEqual(item.errorDisplayText, "No RSS or Atom feed was found for this URL.")
    }

    func testUnseenCountDefaultsToAllLoadedSubmissions() {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }

        let viewModel = SubmissionStatusViewModel(defaults: defaults)
        viewModel.submissions = [
            makeSubmission(id: 1, createdAt: "2026-04-10T10:00:00Z"),
            makeSubmission(id: 2, createdAt: "2026-04-10T09:00:00Z")
        ]

        XCTAssertEqual(viewModel.unseenCount, 2)
    }

    func testMarkCurrentSubmissionsViewedClearsCurrentBadgeAndPersists() {
        let isolated = makeIsolatedDefaults()
        let defaults = isolated.defaults
        defer { clear(isolated.suiteName, defaults: defaults) }

        let viewModel = SubmissionStatusViewModel(defaults: defaults)
        viewModel.submissions = [
            makeSubmission(id: 1, createdAt: "2026-04-10T10:00:00Z"),
            makeSubmission(id: 2, createdAt: "2026-04-10T09:00:00Z")
        ]

        viewModel.markCurrentSubmissionsViewed()

        XCTAssertEqual(viewModel.unseenCount, 0)

        let reloadedViewModel = SubmissionStatusViewModel(defaults: defaults)
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
        status: String = "processing"
    ) -> SubmissionStatusItem {
        SubmissionStatusItem(
            id: id,
            contentType: "article",
            url: "https://example.com/\(id)",
            sourceUrl: nil,
            title: "Submission \(id)",
            status: status,
            errorMessage: nil,
            createdAt: createdAt,
            processedAt: nil,
            submittedVia: "app",
            isSelfSubmission: true
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
