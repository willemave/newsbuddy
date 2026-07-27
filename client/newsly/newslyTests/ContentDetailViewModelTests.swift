import XCTest
@testable import newsly

@MainActor
final class ContentDetailViewModelTests: XCTestCase {
    func testReaderFallbackReusesLoadedSourceBody() async throws {
        let detail = try Self.articleDetail(id: 42)
        let service = BodyRecordingContentDetailService(detail: detail)
        let viewModel = makeViewModel(contentService: service)
        let sourceBody = ContentBody(
            contentId: detail.id,
            variant: "source",
            kind: "article",
            format: "markdown",
            text: "Already loaded source body.",
            updatedAt: nil
        )
        viewModel.updateContentId(detail.id, contentType: detail.contentType)
        viewModel.content = detail
        viewModel.contentBody = sourceBody

        await viewModel.loadReaderBody(for: detail)

        XCTAssertEqual(service.requestedBodyVariants, ["rendered"])
        XCTAssertEqual(viewModel.readerBody?.text, sourceBody.text)
    }

    func testChangingContentCancelsInFlightSourceBody() async throws {
        let detail = try Self.articleDetail(id: 42)
        let service = BodyRecordingContentDetailService(
            detail: detail,
            blockSourceBody: true
        )
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(detail.id, contentType: detail.contentType)

        await viewModel.loadContent()
        await waitUntil { service.sourceRequestCount == 1 }

        viewModel.updateContentId(99, contentType: .article)
        await waitUntil { service.sourceCancellationCount == 1 }

        XCTAssertEqual(service.sourceRequestCount, 1)
        XCTAssertEqual(service.sourceCancellationCount, 1)
        XCTAssertNil(viewModel.contentBody)
    }

    func testAddRelevantLinkToReadLaterMarksLinkAsAddedOnSuccess() async {
        var receivedURL: URL?
        var receivedTitle: String?
        let viewModel = makeViewModel(
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
        let viewModel = makeViewModel(
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
        let viewModel = makeViewModel(
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
        let viewModel = makeViewModel(
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

    func testMediumShareMarkdownIncludesLongformArtifactExtras() throws {
        let detail = try Self.decodeDetail(
            from: """
            {
              "id": 42,
              "content_type": "article",
              "url": "https://example.com/longform-artifact",
              "title": "Artifact Article",
              "display_title": "Artifact Article",
              "source": "Example",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {},
              "created_at": "2026-06-09T10:00:00Z",
              "updated_at": null,
              "processed_at": "2026-06-09T10:05:00Z",
              "checked_out_by": null,
              "checked_out_at": null,
              "publication_date": "2026-06-09T09:00:00Z",
              "is_read": false,
              "is_saved_to_knowledge": false,
              "summary": null,
              "short_summary": null,
              "summary_kind": "longform_artifact",
              "summary_version": 1,
              "structured_summary": null,
              "longform_artifact": {
                "title": "Artifact Article",
                "one_line": "A concise preview that should be used only when overview is absent.",
                "ask": "judge",
                "artifact": {
                  "type": "argument",
                  "payload": {
                    "overview": "This overview explains the source argument and why the reader should inspect the evidence.",
                    "quotes": [
                      {
                        "text": "The first source quote gives the reader concrete evidence.",
                        "attribution": "Source A"
                      }
                    ],
                    "extras": {
                      "thesis": "The source argues that reliable workflows matter more than isolated demos.",
                      "evidence": ["The article cites adoption data from a named workflow."],
                      "mental_model": ["Judge the system by repeated workflow reliability."],
                      "counterpoint": "A fair objection is that demos can still expose important capabilities.",
                      "arguments": ["The argument is supported by operational examples."]
                    },
                    "key_points": [
                      {
                        "heading": "Workflow Reliability",
                        "content": "The piece says repeated reliability matters more than isolated performance."
                      }
                    ],
                    "takeaway": "Judge the claim by its evidence and tradeoffs."
                  }
                }
              },
              "feed_preview": null,
              "artifact_type": "argument",
              "preview_bullets": null,
              "reason_to_read": null,
              "bullet_points": [],
              "quotes": [],
              "topics": [],
              "full_markdown": null,
              "body_available": false,
              "body_kind": null,
              "body_format": null,
              "news_article_url": null,
              "news_discussion_url": null,
              "news_key_points": [],
              "news_summary": null,
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )
        let markdown = try XCTUnwrap(
            ShareMarkdownBuilder(content: detail, contentBody: nil)
                .markdown(for: .medium)
        )

        XCTAssertTrue(markdown.contains("## Summary\nThis overview explains"), markdown)
        XCTAssertTrue(markdown.contains("## Takeaway\nJudge the claim by its evidence and tradeoffs."), markdown)
        XCTAssertTrue(
            markdown.contains(
                "- Workflow Reliability: The piece says repeated reliability matters more than isolated performance."
            ),
            markdown
        )
        XCTAssertTrue(markdown.contains("## Source Quotes\n> The first source quote gives the reader concrete evidence."), markdown)
        XCTAssertTrue(markdown.contains("> - Source A"), markdown)
        XCTAssertTrue(markdown.contains("## Extra"), markdown)
        XCTAssertTrue(markdown.contains("### Evidence\n- The article cites adoption data from a named workflow."), markdown)
        XCTAssertTrue(markdown.contains("### Mental Model\n- Judge the system by repeated workflow reliability."), markdown)
        XCTAssertTrue(
            markdown.contains(
                "### Counter Arguments\n- Counterpoint: A fair objection is that demos can still expose important capabilities."
            ),
            markdown
        )
        XCTAssertTrue(
            markdown.contains(
                "### Supporting Arguments\n- Arguments: The argument is supported by operational examples."
            ),
            markdown
        )
        XCTAssertTrue(
            markdown.contains(
                "### Thesis\n- The source argues that reliable workflows matter more than isolated demos."
            ),
            markdown
        )
        XCTAssertTrue(markdown.contains("Link: https://example.com/longform-artifact"), markdown)
    }

    func testShareMarkdownBuilderUsesLoadedBodyBeforeFallbackMarkdown() throws {
        let detail = try Self.decodeDetail(
            from: """
            {
              "id": 43,
              "content_type": "article",
              "url": "https://example.com/body-article",
              "title": "Body Article",
              "display_title": "Body Article",
              "source": "Example",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {},
              "created_at": "2026-06-09T10:00:00Z",
              "updated_at": null,
              "processed_at": "2026-06-09T10:05:00Z",
              "checked_out_by": null,
              "checked_out_at": null,
              "publication_date": null,
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
              "full_markdown": "Fallback markdown body",
              "body_available": true,
              "body_kind": "article",
              "body_format": "markdown",
              "news_article_url": null,
              "news_discussion_url": null,
              "news_key_points": [],
              "news_summary": null,
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )
        let body = ContentBody(
            contentId: detail.id,
            variant: "reader",
            kind: "article",
            format: "markdown",
            text: "Fetched article body.",
            updatedAt: nil
        )

        let markdown = try XCTUnwrap(
            ShareMarkdownBuilder(content: detail, contentBody: body)
                .markdown(for: .full)
        )

        XCTAssertTrue(markdown.contains("## Full Article\n\nFetched article body."), markdown)
        XCTAssertFalse(markdown.contains("Fallback markdown body"), markdown)
    }

    private func makeViewModel(
        submitLinkToLongFormHandler: @escaping LinkSubmissionCoordinator.SubmitHandler
    ) -> ContentDetailViewModel {
        ContentDetailViewModel(
            contentService: StubContentDetailService(),
            feedSubscriptionService: StubDetectedFeedSubscriber(),
            toastPresenter: StubToastPresenter(),
            submitLinkToLongFormHandler: submitLinkToLongFormHandler
        )
    }

    private func makeViewModel(
        contentService: any ContentDetailServicing
    ) -> ContentDetailViewModel {
        ContentDetailViewModel(
            contentService: contentService,
            feedSubscriptionService: StubDetectedFeedSubscriber(),
            toastPresenter: StubToastPresenter()
        )
    }

    private func waitUntil(
        _ predicate: @escaping () -> Bool,
        attempts: Int = 100
    ) async {
        for _ in 0..<attempts {
            if predicate() {
                return
            }
            try? await Task.sleep(nanoseconds: 1_000_000)
        }
        XCTFail("Condition was not satisfied before timeout")
    }

    nonisolated private static func submitResponse(
        alreadyExists: Bool = false,
        taskId: Int? = 99
    ) -> SubmitContentResponse {
        SubmitContentResponse(
            contentId: 42,
            contentType: .article,
            status: .new,
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

    private static func decodeDetail(from json: String) throws -> ContentDetail {
        let data = Data(json.utf8)
        return try JSONDecoder().decode(ContentDetail.self, from: data)
    }

    private static func articleDetail(id: Int) throws -> ContentDetail {
        try decodeDetail(
            from: """
            {
              "id": \(id),
              "content_type": "article",
              "url": "https://example.com/article-\(id)",
              "title": "Article \(id)",
              "display_title": "Article \(id)",
              "source": "Example",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {},
              "created_at": "2026-07-25T10:00:00Z",
              "updated_at": null,
              "processed_at": "2026-07-25T10:01:00Z",
              "checked_out_by": null,
              "checked_out_at": null,
              "publication_date": null,
              "is_read": true,
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
              "body_available": true,
              "body_kind": "article",
              "body_format": "markdown",
              "news_article_url": null,
              "news_discussion_url": null,
              "news_key_points": [],
              "news_summary": null,
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )
    }
}

private enum StubDetailServiceError: Error {
    case unexpectedCall
}

private class StubContentDetailService: ContentDetailServicing {
    func submitContent(
        url: URL,
        contentType: String?,
        title: String?,
        platform: String?
    ) async throws -> SubmitContentResponse {
        throw StubDetailServiceError.unexpectedCall
    }

    func fetchContentDetail(id: Int) async throws -> ContentDetail {
        throw StubDetailServiceError.unexpectedCall
    }

    func fetchNewsItemDetail(id: Int) async throws -> ContentDetail {
        throw StubDetailServiceError.unexpectedCall
    }

    func fetchContentBody(
        id: Int,
        variant: String,
        contentType: APIContentType?
    ) async throws -> ContentBody {
        throw StubDetailServiceError.unexpectedCall
    }

    func trackContentOpened(
        contentId: Int,
        surface: String,
        contextData: [String: Any]
    ) async throws -> TrackContentInteractionResponse {
        throw StubDetailServiceError.unexpectedCall
    }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw StubDetailServiceError.unexpectedCall
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw StubDetailServiceError.unexpectedCall
    }

    func convertNewsToArticle(id: Int) async throws -> ConvertNewsResponse {
        throw StubDetailServiceError.unexpectedCall
    }

    func convertNewsItemToArticle(id: Int) async throws -> ConvertNewsResponse {
        throw StubDetailServiceError.unexpectedCall
    }

    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse {
        throw StubDetailServiceError.unexpectedCall
    }
}

private final class BodyRecordingContentDetailService: StubContentDetailService {
    private let detail: ContentDetail
    private let blockSourceBody: Bool
    private let stateLock = NSLock()
    private var bodyVariants: [String] = []
    private var sourceCancellations = 0

    init(detail: ContentDetail, blockSourceBody: Bool = false) {
        self.detail = detail
        self.blockSourceBody = blockSourceBody
    }

    var requestedBodyVariants: [String] {
        stateLock.withLock { bodyVariants }
    }

    var sourceRequestCount: Int {
        stateLock.withLock { bodyVariants.count { $0 == "source" } }
    }

    var sourceCancellationCount: Int {
        stateLock.withLock { sourceCancellations }
    }

    override func fetchContentDetail(id: Int) async throws -> ContentDetail {
        detail
    }

    override func fetchContentBody(
        id: Int,
        variant: String,
        contentType: APIContentType?
    ) async throws -> ContentBody {
        stateLock.withLock {
            bodyVariants.append(variant)
        }

        if variant == "source", blockSourceBody {
            do {
                try await Task.sleep(nanoseconds: 60_000_000_000)
            } catch {
                stateLock.withLock {
                    sourceCancellations += 1
                }
                throw error
            }
        }

        return ContentBody(
            contentId: id,
            variant: variant,
            kind: "article",
            format: "markdown",
            text: variant == "rendered" ? "" : "Source body",
            updatedAt: nil
        )
    }

    override func trackContentOpened(
        contentId: Int,
        surface: String,
        contextData: [String: Any]
    ) async throws -> TrackContentInteractionResponse {
        TrackContentInteractionResponse(
            status: "ok",
            recorded: true,
            interactionId: "interaction-\(contentId)",
            analyticsInteractionId: nil
        )
    }
}

private final class StubDetectedFeedSubscriber: DetectedFeedSubscribing {
    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig {
        throw StubDetailServiceError.unexpectedCall
    }
}

@MainActor
private final class StubToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
