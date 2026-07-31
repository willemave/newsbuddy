import XCTest
@testable import newsly

@MainActor
final class LinkSubmissionCoordinatorTests: XCTestCase {
    func testAddedLinkIsNotSubmittedAgain() async {
        var attempts = 0
        let coordinator = LinkSubmissionCoordinator(
            submitLinkToLongFormHandler: { _, _ in
                attempts += 1
                return Self.submitResponse()
            },
            toastPresenter: StubLinkSubmissionToastPresenter()
        )
        let link = Self.relevantLink(
            url: "https://example.com/already-added",
            title: "Already added"
        )

        await coordinator.addRelevantLinkToReadLater(link)
        await coordinator.addRelevantLinkToReadLater(link)

        XCTAssertEqual(attempts, 1)
        XCTAssertEqual(coordinator.state(for: link.id), .added)
    }

    func testResetClearsLinkStateAndPublishesChange() async {
        var changeNotifications = 0
        let coordinator = LinkSubmissionCoordinator(
            submitLinkToLongFormHandler: { _, _ in
                Self.submitResponse()
            },
            toastPresenter: StubLinkSubmissionToastPresenter()
        )
        coordinator.onStateWillChange = {
            changeNotifications += 1
        }
        let link = Self.relevantLink(
            url: "https://example.com/reset-link",
            title: "Reset link"
        )

        await coordinator.addRelevantLinkToReadLater(link)
        XCTAssertEqual(coordinator.state(for: link.id), .added)

        changeNotifications = 0
        coordinator.reset()

        XCTAssertEqual(coordinator.state(for: link.id), .idle)
        XCTAssertEqual(changeNotifications, 1)
    }

    nonisolated private static func submitResponse() -> SubmitContentResponse {
        SubmitContentResponse(
            contentId: 42,
            contentType: .article,
            status: .new,
            platform: nil,
            alreadyExists: false,
            message: "Queued",
            taskId: 99,
            source: "self submission"
        )
    }

    nonisolated private static func relevantLink(url: String, title: String) -> RelevantLink {
        RelevantLink(
            url: url,
            title: title,
            reason: "Supporting context.",
            source: nil
        )
    }
}

@MainActor
private final class StubLinkSubmissionToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
