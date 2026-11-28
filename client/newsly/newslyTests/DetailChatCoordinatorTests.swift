import XCTest
@testable import newsly

@MainActor
final class DetailChatCoordinatorTests: XCTestCase {
    func testStartChatReusesExistingArticleSessionWithoutCreatingNewOne() async {
        let existing = makeSession(id: 42, contentId: 7)
        let service = MockDetailChatService(existingSession: existing)
        let coordinator = DetailChatCoordinator(
            chatSessionManager: makeTestActiveChatSessionManager(),
            chatService: service,
            toastPresenter: RecordingToastPresenter()
        )
        let detail = makeContentDetail(id: 7, contentType: .article)

        let route = await coordinator.startChat(content: detail)

        XCTAssertEqual(route?.sessionId, 42)
        XCTAssertEqual(route?.contentId, 7)
        XCTAssertNil(route?.newsItemId)
        XCTAssertEqual(route?.focusComposerOnAppear, false)
        XCTAssertEqual(service.lookedUpSources, [.content(7)])
        XCTAssertTrue(service.startedArticleIds.isEmpty)
        XCTAssertTrue(service.startedNewsIds.isEmpty)
    }

    func testStartChatReusesExistingNewsSessionWithoutCreatingNewOne() async {
        let existing = makeSession(id: 99, newsItemId: 12)
        let service = MockDetailChatService(existingSession: existing)
        let coordinator = DetailChatCoordinator(
            chatSessionManager: makeTestActiveChatSessionManager(),
            chatService: service,
            toastPresenter: RecordingToastPresenter()
        )
        let detail = makeContentDetail(id: 12, contentType: .news)

        let route = await coordinator.startChat(content: detail)

        XCTAssertEqual(route?.sessionId, 99)
        XCTAssertNil(route?.contentId)
        XCTAssertEqual(route?.newsItemId, 12)
        XCTAssertEqual(route?.focusComposerOnAppear, false)
        XCTAssertEqual(service.lookedUpSources, [.news(12)])
        XCTAssertTrue(service.startedArticleIds.isEmpty)
        XCTAssertTrue(service.startedNewsIds.isEmpty)
    }

    func testStartChatCreatesNewSessionWhenNoneExists() async {
        let created = makeSession(id: 101, contentId: 8)
        let service = MockDetailChatService(existingSession: nil, createdSession: created)
        let coordinator = DetailChatCoordinator(
            chatSessionManager: makeTestActiveChatSessionManager(),
            chatService: service,
            toastPresenter: RecordingToastPresenter()
        )
        let detail = makeContentDetail(id: 8, contentType: .article)

        let route = await coordinator.startChat(content: detail)

        XCTAssertEqual(route?.sessionId, 101)
        XCTAssertEqual(route?.contentId, 8)
        XCTAssertEqual(route?.focusComposerOnAppear, true)
        XCTAssertEqual(service.lookedUpSources, [.content(8)])
        XCTAssertEqual(service.startedArticleIds, [8])
    }

    private func makeSession(
        id: Int,
        contentId: Int? = nil,
        newsItemId: Int? = nil
    ) -> ChatSessionSummary {
        ChatSessionSummary(
            id: id,
            contentId: contentId,
            newsItemId: newsItemId,
            title: "Session \(id)",
            sessionType: "article_brain",
            topic: nil,
            llmProvider: "openai",
            llmModel: "gpt-5",
            createdAt: Date(timeIntervalSince1970: 1_795_800_000),
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isWaitingForContent: false,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }

    private func makeContentDetail(id: Int, contentType: APIContentType) -> ContentDetail {
        let now = Date(timeIntervalSince1970: 1_795_800_000)
        if contentType == .news {
            return ContentDetail(
                api: APINewsItemDetailResponse(
                    id: id,
                    contentType: .news,
                    url: "https://example.com/news/\(id)",
                    sourceUrl: nil,
                    discussionUrl: nil,
                    title: "News \(id)",
                    displayTitle: "News \(id)",
                    source: "HN",
                    status: .completed,
                    retryCount: 0,
                    metadata: [:],
                    createdAt: now,
                    updatedAt: now,
                    processedAt: now,
                    publicationDate: now,
                    isRead: false,
                    isSavedToKnowledge: false,
                    summary: nil,
                    shortSummary: nil,
                    bodyAvailable: false,
                    bodyKind: nil,
                    bodyFormat: nil,
                    newsArticleUrl: "https://example.com/news/\(id)",
                    newsDiscussionUrl: nil,
                    newsKeyPoints: nil,
                    newsSummary: nil,
                    canSubscribe: false
                )
            )
        }
        return ContentDetail(
            api: APIContentDetailResponse(
                id: id,
                contentType: contentType,
                url: "https://example.com/article/\(id)",
                sourceUrl: nil,
                discussionUrl: nil,
                title: "Article \(id)",
                displayTitle: "Article \(id)",
                source: "Blog",
                status: .completed,
                errorMessage: nil,
                retryCount: 0,
                metadata: [:],
                createdAt: now,
                updatedAt: now,
                processedAt: now,
                checkedOutBy: nil,
                checkedOutAt: nil,
                publicationDate: now,
                isRead: false,
                isSavedToKnowledge: false,
                summary: nil,
                shortSummary: nil,
                summaryKind: nil,
                summaryVersion: nil,
                structuredSummary: nil,
                longformArtifact: nil,
                feedPreview: nil,
                artifactType: nil,
                previewBullets: nil,
                reasonToRead: nil,
                bulletPoints: [],
                quotes: [],
                topics: [],
                fullMarkdown: nil,
                bodyAvailable: false,
                bodyKind: nil,
                bodyFormat: nil,
                newsArticleUrl: nil,
                newsDiscussionUrl: nil,
                newsKeyPoints: nil,
                newsSummary: nil,
                imageUrl: nil,
                thumbnailUrl: nil,
                detectedFeed: nil,
                canSubscribe: false
            )
        )
    }
}

@MainActor
private final class MockDetailChatService: DetailChatServicing {
    let existingSession: ChatSessionSummary?
    let createdSession: ChatSessionSummary?
    var lookedUpSources: [KnowledgeSource] = []
    var startedArticleIds: [Int] = []
    var startedNewsIds: [Int] = []

    init(
        existingSession: ChatSessionSummary?,
        createdSession: ChatSessionSummary? = nil
    ) {
        self.existingSession = existingSession
        self.createdSession = createdSession
    }

    func existingSourceChat(for source: KnowledgeSource) async throws -> ChatSessionSummary? {
        lookedUpSources.append(source)
        return existingSession
    }

    func startArticleChat(
        contentId: Int,
        provider: ChatModelProvider
    ) async throws -> ChatSessionSummary {
        startedArticleIds.append(contentId)
        return createdSession ?? existingSession!
    }

    func startNewsChat(
        newsItemId: Int,
        provider: ChatModelProvider
    ) async throws -> ChatSessionSummary {
        startedNewsIds.append(newsItemId)
        return createdSession ?? existingSession!
    }

    func startTopicChat(
        contentId: Int,
        topic: String,
        provider: ChatModelProvider
    ) async throws -> ChatSessionSummary {
        fatalError("unused in test")
    }

    func startNewsTopicChat(
        newsItemId: Int,
        topic: String,
        provider: ChatModelProvider
    ) async throws -> ChatSessionSummary {
        fatalError("unused in test")
    }

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        fatalError("unused in test")
    }
}

@MainActor
private final class RecordingToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showSuccess(_ message: String) {}
    func showError(_ message: String) {}
}
