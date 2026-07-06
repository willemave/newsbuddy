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
        let link = Self.discussionLink(
            url: "https://example.com/already-added",
            title: "Already added"
        )

        await coordinator.addDiscussionLinkToLongForm(link)
        await coordinator.addDiscussionLinkToLongForm(link)

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
        let link = Self.discussionLink(
            url: "https://example.com/reset-link",
            title: "Reset link"
        )

        await coordinator.addDiscussionLinkToLongForm(link)
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

    nonisolated private static func discussionLink(url: String, title: String) -> DiscussionLink {
        DiscussionLink(
            url: url,
            source: "comment",
            commentID: "c1",
            groupLabel: nil,
            title: title
        )
    }
}

@MainActor
private final class StubLinkSubmissionToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
