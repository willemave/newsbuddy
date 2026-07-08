import XCTest
@testable import newsly

@MainActor
final class DiscussionSheetCoordinatorTests: XCTestCase {
    func testResolveCommentsDestinationUsesInlineSummaryWhenSummaryExists() async throws {
        let discussion = Self.discussion(summary: Self.summary)
        let service = StubDiscussionService(discussion: discussion)
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent()

        let destination = await coordinator.resolveCommentsDestination(
            content: content,
            fallbackURL: Self.discussionURL,
            currentContentId: content.id
        )

        XCTAssertEqual(destination, .inlineSummary)
        XCTAssertEqual(service.fetchCallCount, 1)
    }

    func testResolveCommentsDestinationPresentsSheetForSummarylessPayload() async throws {
        let discussion = Self.discussion(
            comments: [
                DiscussionComment(
                    commentID: "c1",
                    parentID: nil,
                    author: "Reader",
                    text: "Useful context.",
                    compactText: nil,
                    depth: 0,
                    createdAt: nil,
                    sourceURL: nil
                )
            ]
        )
        let service = StubDiscussionService(discussion: discussion)
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent()

        let destination = await coordinator.resolveCommentsDestination(
            content: content,
            fallbackURL: Self.discussionURL,
            currentContentId: content.id
        )

        XCTAssertEqual(destination, .sheet)
        XCTAssertEqual(service.fetchCallCount, 1)
    }

    func testResolveCommentsDestinationPresentsSheetAfterLoadFailure() async throws {
        let service = StubDiscussionService(error: URLError(.timedOut))
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent()

        let destination = await coordinator.resolveCommentsDestination(
            content: content,
            fallbackURL: Self.discussionURL,
            currentContentId: content.id
        )

        XCTAssertEqual(destination, .sheet)
        XCTAssertEqual(coordinator.unavailableMessage, "Comments could not be loaded right now.")
    }

    func testResolveCommentsDestinationPresentsSheetWhenContentChangesDuringLoad() async throws {
        let discussion = Self.discussion(summary: Self.summary)
        let service = StubDiscussionService(discussion: discussion)
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent(id: 1)

        let destination = await coordinator.resolveCommentsDestination(
            content: content,
            fallbackURL: Self.discussionURL,
            currentContentId: 2
        )

        XCTAssertEqual(destination, .sheet)
        XCTAssertNil(coordinator.payload)
    }

    func testResolveCommentsDestinationUsesCachedPayloadWithoutFetching() async throws {
        let service = StubDiscussionService(error: StubDiscussionServiceError.unexpectedCall)
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent()
        coordinator.payload = Self.discussion(summary: Self.summary)

        let destination = await coordinator.resolveCommentsDestination(
            content: content,
            fallbackURL: Self.discussionURL,
            currentContentId: content.id
        )

        XCTAssertEqual(destination, .inlineSummary)
        XCTAssertEqual(service.fetchCallCount, 0)
    }

    func testPendingCommentsNavigationWaitsUntilPayloadLoadsThenScrollsInlineSummary() async throws {
        let service = StubDiscussionService(discussion: Self.discussion(summary: Self.summary))
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent()

        coordinator.requestCommentsNavigation(content: content, fallbackURL: Self.discussionURL)

        XCTAssertEqual(coordinator.commentsNavigationAction(for: content), .waitForPayload)

        await coordinator.loadPendingCommentsNavigation(
            content: content,
            currentContentId: content.id
        )

        XCTAssertEqual(coordinator.commentsNavigationAction(for: content), .scrollInlineSummary)
        XCTAssertEqual(coordinator.commentsNavigationAction(for: content), .none)
        XCTAssertEqual(service.fetchCallCount, 1)
    }

    func testPendingCommentsNavigationPresentsSheetForSummarylessPayload() async throws {
        let service = StubDiscussionService(
            discussion: Self.discussion(
                comments: [
                    DiscussionComment(
                        commentID: "c1",
                        parentID: nil,
                        author: "Reader",
                        text: "Useful context.",
                        compactText: nil,
                        depth: 0,
                        createdAt: nil,
                        sourceURL: nil
                    )
                ]
            )
        )
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent()

        coordinator.requestCommentsNavigation(content: content, fallbackURL: Self.discussionURL)
        await coordinator.loadPendingCommentsNavigation(
            content: content,
            currentContentId: content.id
        )

        XCTAssertEqual(coordinator.commentsNavigationAction(for: content), .presentSheet)
        XCTAssertEqual(coordinator.commentsNavigationAction(for: content), .none)
    }

    func testPendingCommentsNavigationResetsWhenCoordinatorResets() async throws {
        let service = StubDiscussionService(discussion: Self.discussion(summary: Self.summary))
        let coordinator = DiscussionSheetCoordinator(contentService: service)
        let content = try Self.newsContent()

        coordinator.requestCommentsNavigation(content: content, fallbackURL: Self.discussionURL)
        coordinator.reset()

        XCTAssertEqual(coordinator.commentsNavigationAction(for: content), .none)
    }

    private static let discussionURL = URL(string: "https://news.ycombinator.com/item?id=1")!
    private static let summary = DiscussionSummary(
        overview: "Readers largely agree.",
        topics: [],
        notableLinks: [],
        representativeComments: [],
        externalDiscussionURL: nil,
        generatedAt: nil
    )

    private static func discussion(
        comments: [DiscussionComment] = [],
        summary: DiscussionSummary? = nil
    ) -> ContentDiscussion {
        ContentDiscussion(
            contentId: 1,
            status: "completed",
            mode: "comments",
            platform: "hackernews",
            sourceURL: nil,
            discussionURL: "https://news.ycombinator.com/item?id=1",
            fetchedAt: nil,
            errorMessage: nil,
            comments: comments,
            discussionGroups: [],
            links: [],
            summary: summary,
            stats: [:]
        )
    }

    private static func newsContent(id: Int = 1) throws -> ContentDetail {
        let json = """
        {
          "id": \(id),
          "content_type": "news",
          "url": "https://example.com/story-\(id)",
          "title": "Example Story",
          "display_title": "Example Story",
          "source": "Example",
          "status": "completed",
          "error_message": null,
          "retry_count": 0,
          "metadata": {},
          "created_at": "2026-07-08T10:00:00Z",
          "updated_at": null,
          "processed_at": "2026-07-08T10:05:00Z",
          "checked_out_by": null,
          "checked_out_at": null,
          "publication_date": "2026-07-08T09:00:00Z",
          "is_read": false,
          "is_saved_to_knowledge": false,
          "summary": null,
          "short_summary": null,
          "summary_kind": null,
          "summary_version": null,
          "structured_summary": null,
          "longform_artifact": null,
          "feed_preview": null,
          "artifact_type": null,
          "preview_bullets": null,
          "reason_to_read": null,
          "bullet_points": [],
          "quotes": [],
          "topics": [],
          "full_markdown": null,
          "body_available": false,
          "body_kind": null,
          "body_format": null,
          "image_url": null,
          "thumbnail_url": null,
          "news_article_url": "https://example.com/story-\(id)",
          "news_discussion_url": "\(discussionURL.absoluteString)",
          "news_key_points": [],
          "news_summary": null,
          "detected_feed": null,
          "can_subscribe": false
        }
        """
        return try JSONDecoder().decode(ContentDetail.self, from: Data(json.utf8))
    }
}

private final class StubDiscussionService: ContentDiscussionServicing {
    private let discussion: ContentDiscussion?
    private let error: Error?
    private(set) var fetchCallCount = 0

    init(discussion: ContentDiscussion? = nil, error: Error? = nil) {
        self.discussion = discussion
        self.error = error
    }

    func refreshContentDiscussion(id: Int, contentType: APIContentType?) async throws -> ContentDiscussion {
        throw StubDiscussionServiceError.unexpectedCall
    }

    func fetchContentDiscussion(id: Int, contentType: APIContentType?) async throws -> ContentDiscussion {
        fetchCallCount += 1
        if let error {
            throw error
        }
        guard let discussion else {
            throw StubDiscussionServiceError.unexpectedCall
        }
        return discussion
    }
}

private enum StubDiscussionServiceError: Error {
    case unexpectedCall
}
