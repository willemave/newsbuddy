import XCTest
@testable import newsly

@MainActor
final class ContentDetailViewModelTests: XCTestCase {
    func testAddDiscussionLinkToLongFormMarksLinkAsAddedOnSuccess() async {
        var receivedURL: URL?
        var receivedTitle: String?
        let viewModel = ContentDetailViewModel(
            submitLinkToLongFormHandler: { url, title in
                receivedURL = url
                receivedTitle = title
                return SubmitContentResponse(
                    contentId: 42,
                    contentType: "article",
                    status: "new",
                    platform: nil,
                    alreadyExists: false,
                    message: "Queued",
                    taskId: 99,
                    source: "self submission"
                )
            }
        )
        let link = DiscussionLink(
            url: "https://example.com/linked-story",
            source: "comment",
            commentID: "c1",
            groupLabel: nil,
            title: "Linked story"
        )

        await viewModel.addDiscussionLinkToLongForm(link)

        XCTAssertEqual(receivedURL?.absoluteString, link.url)
        XCTAssertEqual(receivedTitle, "Linked story")
        XCTAssertEqual(viewModel.discussionLinkAddState(for: link.id), .added)
    }

    func testUpdateContentIdClearsDiscussionLinkState() async {
        let viewModel = ContentDetailViewModel(
            submitLinkToLongFormHandler: { _, _ in
                SubmitContentResponse(
                    contentId: 42,
                    contentType: "article",
                    status: "new",
                    platform: nil,
                    alreadyExists: true,
                    message: "Existing",
                    taskId: nil,
                    source: "self submission"
                )
            }
        )
        let link = DiscussionLink(
            url: "https://example.com/linked-story",
            source: "comment",
            commentID: "c1",
            groupLabel: nil,
            title: "Linked story"
        )

        await viewModel.addDiscussionLinkToLongForm(link)
        viewModel.updateContentId(99, contentType: .news)

        XCTAssertEqual(viewModel.discussionLinkAddState(for: link.id), .idle)
    }
}
