import XCTest
@testable import newsly

@MainActor
final class LinkSubmissionCoordinatorTests: XCTestCase {
    func testAddedLinkIsNotSubmittedAgain() async {
        var attempts = 0
        let service = LinkSubmissionContentService { _, _ in
            attempts += 1
            return Self.submitResponse()
        }
        let coordinator = LinkSubmissionCoordinator(
            contentService: service,
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

    func testResetClearsLinkState() async {
        let coordinator = LinkSubmissionCoordinator(
            contentService: LinkSubmissionContentService { _, _ in
                Self.submitResponse()
            },
            toastPresenter: StubLinkSubmissionToastPresenter()
        )
        let link = Self.relevantLink(
            url: "https://example.com/reset-link",
            title: "Reset link"
        )

        await coordinator.addRelevantLinkToReadLater(link)
        XCTAssertEqual(coordinator.state(for: link.id), .added)

        coordinator.reset()

        XCTAssertEqual(coordinator.state(for: link.id), .idle)
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

private final class LinkSubmissionContentService: ContentDetailServicing {
    private let submit: (URL, String?) async throws -> SubmitContentResponse

    init(submit: @escaping (URL, String?) async throws -> SubmitContentResponse) {
        self.submit = submit
    }

    func submitContent(url: URL, title: String?, platform: String?) async throws -> SubmitContentResponse {
        try await submit(url, title)
    }

    func fetchContentDetail(id: Int) async throws -> ContentDetail { throw CancellationError() }
    func fetchNewsItemDetail(id: Int) async throws -> ContentDetail { throw CancellationError() }
    func fetchContentBody(id: Int, variant: String, contentType: APIContentType?) async throws -> ContentBody { throw CancellationError() }
    func trackContentOpened(contentId: Int, surface: String, contextData: [String: Any]) async throws -> TrackContentInteractionResponse { throw CancellationError() }
    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse { throw CancellationError() }
    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse { throw CancellationError() }
    func convertNewsToArticle(id: Int) async throws -> ConvertNewsResponse { throw CancellationError() }
    func convertNewsItemToArticle(id: Int) async throws -> ConvertNewsResponse { throw CancellationError() }
    func downloadMoreFromSeries(contentId: Int, count: Int) async throws -> DownloadMoreResponse { throw CancellationError() }
}

@MainActor
private final class StubLinkSubmissionToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
