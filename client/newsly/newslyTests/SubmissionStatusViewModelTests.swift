import Foundation
import XCTest
@testable import newsly

@MainActor
final class SubmissionStatusViewModelTests: XCTestCase {
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

    private func makeSubmission(id: Int, createdAt: String) -> SubmissionStatusItem {
        SubmissionStatusItem(
            id: id,
            contentType: "article",
            url: "https://example.com/\(id)",
            sourceUrl: nil,
            title: "Submission \(id)",
            status: "processing",
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
