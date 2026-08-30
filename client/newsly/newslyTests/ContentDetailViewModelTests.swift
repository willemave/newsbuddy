import XCTest
@testable import newsly

@MainActor
final class ContentDetailViewModelTests: XCTestCase {
    func testInitialTransportCancellationIsSilentAndNextLoadSucceeds() async throws {
        let detail = try Self.articleDetail(id: 42)
        let service = CancelledThenSuccessfulContentDetailService(detail: detail)
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(detail.id, contentType: detail.contentType)

        await viewModel.loadContent()

        XCTAssertFalse(viewModel.isLoading)
        XCTAssertNil(viewModel.content)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.initialLoadPhase, .idle)

        await viewModel.loadContent()

        XCTAssertEqual(viewModel.content?.id, detail.id)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(service.fetchCount, 2)
    }

    func testConcurrentLoadsCoalesceAndCommitDependentWorkOnce() async throws {
        let detail = try Self.articleDetail(id: 42)
        let gate = ContentDetailAsyncGate()
        let service = BlockingContentDetailService(detail: detail, gate: gate)
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(detail.id, contentType: detail.contentType)

        let first = Task { await viewModel.loadContent() }
        await waitUntil { service.fetchCount == 1 }
        let second = Task { await viewModel.loadContent() }
        try? await Task.sleep(nanoseconds: 10_000_000)
        XCTAssertEqual(service.fetchCount, 1)

        await gate.open()
        await first.value
        await second.value
        await waitUntil { service.trackOpenedCount == 1 }

        XCTAssertEqual(viewModel.content?.id, detail.id)
        XCTAssertEqual(service.fetchCount, 1)
        XCTAssertEqual(service.trackOpenedCount, 1)
    }

    func testRevalidationFailureRetainsReadableContent() async throws {
        let detail = try Self.articleDetail(id: 42)
        let service = SequencedContentDetailService(
            detailResults: [
                .success(detail),
                .failure(URLError(.notConnectedToInternet)),
            ]
        )
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(detail.id, contentType: detail.contentType)

        await viewModel.loadContent()
        await viewModel.revalidateContent()

        XCTAssertEqual(viewModel.content?.id, detail.id)
        XCTAssertNil(viewModel.errorMessage)
        guard case .failure = viewModel.revalidationPhase else {
            return XCTFail("Expected a nonblocking revalidation failure")
        }
        XCTAssertEqual(service.fetchCount, 2)
        XCTAssertEqual(service.trackOpenedCount, 1)
    }

    func testLateResultForReplacedContentKeyCannotPublish() async throws {
        let firstDetail = try Self.articleDetail(id: 42)
        let secondDetail = try Self.articleDetail(id: 99)
        let firstGate = ContentDetailAsyncGate()
        let service = ReplacingContentDetailService(
            firstDetail: firstDetail,
            secondDetail: secondDetail,
            firstGate: firstGate
        )
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(firstDetail.id, contentType: firstDetail.contentType)

        let firstLoad = Task { await viewModel.loadContent() }
        await waitUntil { service.requestedIDs == [firstDetail.id] }
        viewModel.updateContentId(secondDetail.id, contentType: secondDetail.contentType)
        await viewModel.loadContent()
        XCTAssertEqual(viewModel.content?.id, secondDetail.id)

        await firstGate.open()
        await firstLoad.value

        XCTAssertEqual(viewModel.content?.id, secondDetail.id)
        XCTAssertEqual(service.requestedIDs, [firstDetail.id, secondDetail.id])
    }

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

    func testUnspecifiedTypeHintPublishesFetchedSourceBody() async throws {
        let detail = try Self.articleDetail(id: 42)
        let service = BodyRecordingContentDetailService(detail: detail)
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(detail.id)

        await viewModel.loadContent()
        await waitUntil { viewModel.contentBody != nil }

        XCTAssertEqual(viewModel.contentBody?.contentId, detail.id)
        XCTAssertEqual(viewModel.contentBody?.text, "Source body")
    }

    func testSuspensionFencesNonCooperativeReaderBodyResult() async throws {
        let detail = try Self.articleDetail(id: 42)
        let gate = ContentDetailAsyncGate()
        let service = SequencedReaderBodyContentDetailService(firstGate: gate)
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(detail.id, contentType: detail.contentType)
        viewModel.content = detail

        let load = Task { await viewModel.loadReaderBody(for: detail) }
        await waitUntil { service.requestCount == 1 }
        viewModel.suspendAutomaticReads()
        await gate.open()
        await load.value

        XCTAssertNil(viewModel.readerBody)
        XCTAssertNil(viewModel.readerErrorMessage)
        XCTAssertFalse(viewModel.isLoadingReaderBody)
    }

    func testForcedReaderBodyReplacementFencesNonCooperativeOlderResult() async throws {
        let detail = try Self.articleDetail(id: 42)
        let gate = ContentDetailAsyncGate()
        let service = SequencedReaderBodyContentDetailService(firstGate: gate)
        let viewModel = makeViewModel(contentService: service)
        viewModel.updateContentId(detail.id, contentType: detail.contentType)
        viewModel.content = detail

        let first = Task { await viewModel.loadReaderBody(for: detail) }
        await waitUntil { service.requestCount == 1 }
        await viewModel.loadReaderBody(for: detail, force: true)

        XCTAssertEqual(viewModel.readerBody?.text, "new reader body")

        await gate.open()
        await first.value

        XCTAssertEqual(viewModel.readerBody?.text, "new reader body")
        XCTAssertNil(viewModel.readerErrorMessage)
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

    func testDetectedFeedSubscriptionUsesTypedAlreadySubscribedOutcome() async throws {
        let detail = try Self.articleDetail(id: 42, includesDetectedFeed: true)
        let subscriber = StubDetectedFeedSubscriber(
            result: .success(Self.scraperConfig(subscriptionOutcome: .already_subscribed))
        )
        let viewModel = ContentDetailViewModel(
            contentId: detail.id,
            contentType: detail.contentType,
            contentService: StubContentDetailService(),
            feedSubscriptionService: subscriber,
            toastPresenter: StubToastPresenter()
        )
        viewModel.content = detail

        await viewModel.subscribeToDetectedFeed()

        XCTAssertTrue(viewModel.feedSubscriptionSuccess)
        XCTAssertEqual(viewModel.feedSubscriptionSuccessMessage, "This source was already in your feed")
        XCTAssertNil(viewModel.feedSubscriptionError)
        XCTAssertFalse(viewModel.isSubscribingToFeed)
    }

    func testDetectedFeedSubscriptionDoesNotExposeBackendError() async throws {
        let detail = try Self.articleDetail(id: 42, includesDetectedFeed: true)
        let subscriber = StubDetectedFeedSubscriber(
            result: .failure(
                ClientFailure.http(statusCode: 500, detail: "secret backend detail")
            )
        )
        let viewModel = ContentDetailViewModel(
            contentId: detail.id,
            contentType: detail.contentType,
            contentService: StubContentDetailService(),
            feedSubscriptionService: subscriber,
            toastPresenter: StubToastPresenter()
        )
        viewModel.content = detail

        await viewModel.subscribeToDetectedFeed()

        XCTAssertFalse(viewModel.feedSubscriptionSuccess)
        XCTAssertEqual(viewModel.feedSubscriptionError, "Couldn't subscribe. Please try again.")
        XCTAssertFalse(viewModel.feedSubscriptionError?.contains("secret") == true)
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

    private static func decodeDetail(from json: String) throws -> ContentDetail {
        let data = Data(json.utf8)
        return try JSONDecoder().decode(ContentDetail.self, from: data)
    }

    private static func articleDetail(
        id: Int,
        includesDetectedFeed: Bool = false
    ) throws -> ContentDetail {
        let detectedFeed = includesDetectedFeed
            ? #"{"url":"https://example.com/feed.xml","type":"rss","title":"Example Feed","format":"rss"}"#
            : "null"
        return try decodeDetail(
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
              "detected_feed": \(detectedFeed),
              "can_subscribe": \(includesDetectedFeed)
            }
            """
        )
    }

    private static func scraperConfig(
        subscriptionOutcome: APIFeedSubscriptionOutcome?
    ) -> ScraperConfig {
        ScraperConfig(
            id: 7,
            scraperType: "feed",
            config: [:],
            feedUrl: "https://example.com/feed.xml",
            isActive: true,
            createdAt: Date(),
            subscriptionOutcome: subscriptionOutcome
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

@MainActor
private final class SequencedReaderBodyContentDetailService: StubContentDetailService {
    private let firstGate: ContentDetailAsyncGate
    private(set) var requestCount = 0

    init(firstGate: ContentDetailAsyncGate) {
        self.firstGate = firstGate
    }

    override func fetchContentBody(
        id: Int,
        variant: String,
        contentType: APIContentType?
    ) async throws -> ContentBody {
        XCTAssertEqual(variant, "rendered")
        let requestIndex = requestCount
        requestCount += 1
        if requestIndex == 0 {
            // This continuation deliberately ignores task cancellation so the
            // view model's generation fence, rather than cooperative transport,
            // is what protects publication.
            await firstGate.wait()
        }
        return ContentBody(
            contentId: id,
            variant: variant,
            kind: "article",
            format: "markdown",
            text: requestIndex == 0 ? "old reader body" : "new reader body",
            updatedAt: nil
        )
    }
}

private final class CancelledThenSuccessfulContentDetailService: StubContentDetailService {
    private let detail: ContentDetail
    private(set) var fetchCount = 0

    init(detail: ContentDetail) {
        self.detail = detail
    }

    override func fetchContentDetail(id: Int) async throws -> ContentDetail {
        fetchCount += 1
        if fetchCount == 1 {
            throw URLError(.cancelled)
        }
        return detail
    }

    override func fetchContentBody(
        id: Int,
        variant: String,
        contentType: APIContentType?
    ) async throws -> ContentBody {
        ContentBody(
            contentId: id,
            variant: variant,
            kind: "article",
            format: "markdown",
            text: "Source body",
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

private actor ContentDetailAsyncGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !isOpen else { return }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func open() {
        guard !isOpen else { return }
        isOpen = true
        let pending = waiters
        waiters.removeAll()
        for waiter in pending {
            waiter.resume()
        }
    }
}

private class RecordingContentDetailService: StubContentDetailService {
    private let stateLock = NSLock()
    private var recordedTrackOpenedCount = 0

    var trackOpenedCount: Int {
        stateLock.withLock { recordedTrackOpenedCount }
    }

    override func fetchContentBody(
        id: Int,
        variant: String,
        contentType: APIContentType?
    ) async throws -> ContentBody {
        ContentBody(
            contentId: id,
            variant: variant,
            kind: "article",
            format: "markdown",
            text: "Source body",
            updatedAt: nil
        )
    }

    override func trackContentOpened(
        contentId: Int,
        surface: String,
        contextData: [String: Any]
    ) async throws -> TrackContentInteractionResponse {
        stateLock.withLock { recordedTrackOpenedCount += 1 }
        return TrackContentInteractionResponse(
            status: "ok",
            recorded: true,
            interactionId: "interaction-\(contentId)",
            analyticsInteractionId: nil
        )
    }
}

private final class BlockingContentDetailService: RecordingContentDetailService {
    private let detail: ContentDetail
    private let gate: ContentDetailAsyncGate
    private let stateLock = NSLock()
    private var recordedFetchCount = 0

    init(detail: ContentDetail, gate: ContentDetailAsyncGate) {
        self.detail = detail
        self.gate = gate
    }

    var fetchCount: Int {
        stateLock.withLock { recordedFetchCount }
    }

    override func fetchContentDetail(id: Int) async throws -> ContentDetail {
        stateLock.withLock { recordedFetchCount += 1 }
        await gate.wait()
        return detail
    }
}

private final class SequencedContentDetailService: RecordingContentDetailService {
    private let stateLock = NSLock()
    private var detailResults: [Result<ContentDetail, Error>]
    private var recordedFetchCount = 0

    init(detailResults: [Result<ContentDetail, Error>]) {
        self.detailResults = detailResults
    }

    var fetchCount: Int {
        stateLock.withLock { recordedFetchCount }
    }

    override func fetchContentDetail(id: Int) async throws -> ContentDetail {
        let result = stateLock.withLock { () -> Result<ContentDetail, Error> in
            recordedFetchCount += 1
            return detailResults.removeFirst()
        }
        return try result.get()
    }
}

private final class ReplacingContentDetailService: RecordingContentDetailService {
    private let firstDetail: ContentDetail
    private let secondDetail: ContentDetail
    private let firstGate: ContentDetailAsyncGate
    private let stateLock = NSLock()
    private var recordedIDs: [Int] = []

    init(
        firstDetail: ContentDetail,
        secondDetail: ContentDetail,
        firstGate: ContentDetailAsyncGate
    ) {
        self.firstDetail = firstDetail
        self.secondDetail = secondDetail
        self.firstGate = firstGate
    }

    var requestedIDs: [Int] {
        stateLock.withLock { recordedIDs }
    }

    override func fetchContentDetail(id: Int) async throws -> ContentDetail {
        stateLock.withLock { recordedIDs.append(id) }
        if id == firstDetail.id {
            await firstGate.wait()
            return firstDetail
        }
        return secondDetail
    }
}

private final class StubDetectedFeedSubscriber: DetectedFeedSubscribing {
    private let result: Result<ScraperConfig, Error>

    init(result: Result<ScraperConfig, Error> = .failure(StubDetailServiceError.unexpectedCall)) {
        self.result = result
    }

    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig {
        _ = (feedURL, feedType, displayName)
        return try result.get()
    }
}

@MainActor
private final class StubToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
