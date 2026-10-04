import Foundation
import XCTest
@testable import newsly

final class KnowledgeSourceDerivativesTests: XCTestCase {
    func testIndexKeepsContentAndNewsSourcesApart() {
        let index = KnowledgeSourceDerivatives.index(
            chats: [
                makeChat(id: 1, contentId: 7),
                makeChat(id: 2, contentId: 7),
                makeChat(id: 3, newsItemId: 7)
            ],
            decks: [
                makeDeck(id: 4, sourceContentId: 7),
                makeDeck(id: 5, sourceKind: .github_repo, sourceContentId: 8)
            ],
            narrations: [
                makeNarration(id: 6, contentIds: [8, 9], newsItemIds: [7])
            ]
        )

        XCTAssertEqual(index[.content(7)], [.chat, .deck])
        XCTAssertEqual(index[.news(7)], [.chat, .narration])
        XCTAssertEqual(index[.content(8)], [.narration])
        XCTAssertEqual(index[.content(9)], [.narration])
    }

    func testSourceUsesContentTypeNamespace() {
        let news = KnowledgeSource(makeContent(id: 7, contentType: .news))
        let article = KnowledgeSource(makeContent(id: 7, contentType: .article))

        XCTAssertEqual(news, .news(7))
        XCTAssertEqual(news.newsItemId, 7)
        XCTAssertNil(news.contentId)
        XCTAssertEqual(article, .content(7))
        XCTAssertEqual(article.contentId, 7)
        XCTAssertNil(article.newsItemId)
    }

    func testMarkersFollowDisplayOrderAndDescribeResults() {
        let derivatives: KnowledgeSourceDerivatives = [.narration, .chat, .deck]

        XCTAssertEqual(derivatives.actions, [.deck, .chat, .listen])
        XCTAssertEqual(derivatives.accessibilitySummary, "Has deck, chat, narration")
        XCTAssertEqual(KnowledgeSourceDerivatives().accessibilitySummary, "")
    }

    private func makeContent(id: Int, contentType: APIContentType) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: "https://example.com/\(id)",
            title: "Saved source",
            source: "Example",
            platform: nil,
            status: .completed,
            shortSummary: "Summary",
            createdAt: "2026-09-27T12:00:00Z",
            processedAt: nil,
            isRead: false,
            isSavedToKnowledge: true
        )
    }

    private func makeChat(
        id: Int,
        contentId: Int? = nil,
        newsItemId: Int? = nil
    ) -> ChatSessionSummary {
        ChatSessionSummary(
            id: id,
            contentId: contentId,
            newsItemId: newsItemId,
            title: "Chat \(id)",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.5",
            createdAt: Date(timeIntervalSince1970: 1_800_000_000),
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )
    }

    private func makeDeck(
        id: Int,
        sourceKind: LearningDeckSourceKind = .content,
        sourceContentId: Int?
    ) -> LearningDeck {
        LearningDeck(
            id: id,
            title: "Deck \(id)",
            sourceKind: sourceKind,
            sourceURL: nil,
            sourceContentId: sourceContentId,
            sourceTitle: nil,
            sourceMetadata: [:],
            status: .completed,
            shareEnabled: false,
            viewerAvailable: true,
            latestSuccessfulRunId: nil,
            latestRun: nil,
            createdAt: Date(timeIntervalSince1970: 1_800_000_000),
            updatedAt: nil
        )
    }

    private func makeNarration(id: Int, contentIds: [Int], newsItemIds: [Int]) -> AudioEpisode {
        AudioEpisode(
            id: id,
            kind: .custom_narration,
            status: .completed,
            title: "Narration \(id)",
            sourceContentId: nil,
            sourceItemIds: newsItemIds,
            sourceContentIds: contentIds,
            subtitle: nil,
            artworkUrl: nil,
            durationSeconds: nil,
            audioUrl: nil,
            streamUrl: nil,
            scriptText: nil,
            errorMessage: nil,
            createdAt: Date(timeIntervalSince1970: 1_800_000_000),
            updatedAt: nil
        )
    }
}
