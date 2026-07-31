import XCTest
@testable import newsly

@MainActor
final class DiscussionSummaryCoordinatorTests: XCTestCase {
    func testLoadStoredSummaryPublishesSummaryForCurrentContent() async throws {
        let content = try Self.newsContent()
        let discussion = Self.discussion(summary: Self.summary)
        let service = StubDiscussionSummaryService(discussion: discussion)
        let coordinator = DiscussionSummaryCoordinator(contentService: service)

        await coordinator.loadStoredSummary(for: content, currentContentId: content.id)

        XCTAssertEqual(coordinator.inlineSummaryPayload(for: content)?.summary?.overview, "Readers largely agree.")
        XCTAssertEqual(service.fetchCallCount, 1)
    }

    func testLoadStoredSummaryIgnoresSummarylessDiscussion() async throws {
        let content = try Self.newsContent()
        let service = StubDiscussionSummaryService(discussion: Self.discussion(summary: nil))
        let coordinator = DiscussionSummaryCoordinator(contentService: service)

        await coordinator.loadStoredSummary(for: content, currentContentId: content.id)

        XCTAssertNil(coordinator.inlineSummaryPayload(for: content))
    }

    func testLoadStoredSummaryIgnoresResponseAfterContentChanges() async throws {
        let content = try Self.newsContent()
        let service = StubDiscussionSummaryService(discussion: Self.discussion(summary: Self.summary))
        let coordinator = DiscussionSummaryCoordinator(contentService: service)

        await coordinator.loadStoredSummary(for: content, currentContentId: 2)

        XCTAssertNil(coordinator.inlineSummaryPayload(for: content))
    }

    func testResetClearsInlineSummary() async throws {
        let content = try Self.newsContent()
        let service = StubDiscussionSummaryService(discussion: Self.discussion(summary: Self.summary))
        let coordinator = DiscussionSummaryCoordinator(contentService: service)
        await coordinator.loadStoredSummary(for: content, currentContentId: content.id)

        coordinator.reset()

        XCTAssertNil(coordinator.inlineSummaryPayload(for: content))
    }

    private static let summary = DiscussionSummary(
        overview: "Readers largely agree.",
        topics: [
            DiscussionSummaryTopic(
                title: "Practical tradeoffs",
                summary: "Commenters focus on operational simplicity.",
                stance: "supportive"
            )
        ],
        notableLinks: [],
        representativeComments: [],
        externalDiscussionURL: nil,
        generatedAt: nil
    )

    private static func discussion(summary: DiscussionSummary?) -> ContentDiscussion {
        ContentDiscussion(
            contentId: 1,
            status: "completed",
            mode: "comments",
            platform: "hackernews",
            sourceURL: nil,
            discussionURL: "https://news.ycombinator.com/item?id=1",
            fetchedAt: nil,
            errorMessage: nil,
            comments: [],
            discussionGroups: [],
            links: [],
            summary: summary,
            stats: [:]
        )
    }

    private static func newsContent() throws -> ContentDetail {
        let json = """
        {
          "id": 1,
          "content_type": "news",
          "url": "https://example.com/story",
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
          "news_article_url": "https://example.com/story",
          "news_discussion_url": "https://news.ycombinator.com/item?id=1",
          "news_key_points": [],
          "news_summary": null,
          "detected_feed": null,
          "can_subscribe": false
        }
        """
        return try JSONDecoder().decode(ContentDetail.self, from: Data(json.utf8))
    }
}

private final class StubDiscussionSummaryService: ContentDiscussionServicing {
    private let discussion: ContentDiscussion
    private(set) var fetchCallCount = 0

    init(discussion: ContentDiscussion) {
        self.discussion = discussion
    }

    func fetchContentDiscussion(id: Int, contentType: APIContentType?) async throws -> ContentDiscussion {
        fetchCallCount += 1
        return discussion
    }
}
