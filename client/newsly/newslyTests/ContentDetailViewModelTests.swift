import XCTest
@testable import newsly

@MainActor
final class ContentDetailViewModelTests: XCTestCase {
    func testAddRelevantLinkToReadLaterMarksLinkAsAddedOnSuccess() async {
        var receivedURL: URL?
        var receivedTitle: String?
        let viewModel = ContentDetailViewModel(
            submitLinkToLongFormHandler: { url, title in
                receivedURL = url
                receivedTitle = title
                return Self.submitResponse()
            }
        )
        let link = RelevantLink(
            url: "https://example.com/relevant-story",
            title: "Relevant story",
            reason: "Useful supporting context.",
            source: "article"
        )

        await viewModel.addRelevantLinkToReadLater(link)

        XCTAssertEqual(receivedURL?.absoluteString, link.url)
        XCTAssertEqual(receivedTitle, "Relevant story")
        XCTAssertEqual(viewModel.relevantLinkReadLaterState(for: link.id), .added)
    }

    func testAddDiscussionLinkToLongFormMarksLinkAsAddedOnSuccess() async {
        var receivedURL: URL?
        var receivedTitle: String?
        let viewModel = ContentDetailViewModel(
            submitLinkToLongFormHandler: { url, title in
                receivedURL = url
                receivedTitle = title
                return Self.submitResponse()
            }
        )
        let link = Self.discussionLink(
            url: "https://example.com/linked-story",
            title: "Linked story"
        )

        await viewModel.addDiscussionLinkToLongForm(link)

        XCTAssertEqual(receivedURL?.absoluteString, link.url)
        XCTAssertEqual(receivedTitle, "Linked story")
        XCTAssertEqual(viewModel.discussionLinkAddState(for: link.id), .added)
    }

    func testAddDiscussionLinkToLongFormCanRetryAfterFailure() async {
        var attempts = 0
        let viewModel = ContentDetailViewModel(
            submitLinkToLongFormHandler: { _, _ in
                attempts += 1
                if attempts == 1 {
                    throw URLError(.timedOut)
                }
                return Self.submitResponse()
            }
        )
        let link = Self.discussionLink(
            url: "https://example.com/retry-story",
            title: "Retry story"
        )

        await viewModel.addDiscussionLinkToLongForm(link)
        XCTAssertEqual(viewModel.discussionLinkAddState(for: link.id), .failed)

        await viewModel.addDiscussionLinkToLongForm(link)

        XCTAssertEqual(attempts, 2)
        XCTAssertEqual(viewModel.discussionLinkAddState(for: link.id), .added)
    }

    func testUpdateContentIdClearsDiscussionLinkState() async {
        let viewModel = ContentDetailViewModel(
            submitLinkToLongFormHandler: { _, _ in
                Self.submitResponse(alreadyExists: true, taskId: nil)
            }
        )
        let link = Self.discussionLink(
            url: "https://example.com/linked-story",
            title: "Linked story"
        )

        await viewModel.addDiscussionLinkToLongForm(link)
        viewModel.updateContentId(99, contentType: .news)

        XCTAssertEqual(viewModel.discussionLinkAddState(for: link.id), .idle)
        XCTAssertTrue(viewModel.isLoading)
        XCTAssertNil(viewModel.errorMessage)
    }

    nonisolated private static func submitResponse(
        alreadyExists: Bool = false,
        taskId: Int? = 99
    ) -> SubmitContentResponse {
        SubmitContentResponse(
            contentId: 42,
            contentType: "article",
            status: "new",
            platform: nil,
            alreadyExists: alreadyExists,
            message: alreadyExists ? "Existing" : "Queued",
            taskId: taskId,
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
