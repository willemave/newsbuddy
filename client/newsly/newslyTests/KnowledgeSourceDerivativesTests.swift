import Foundation
import XCTest
@testable import newsly

final class KnowledgeSourceDerivativesTests: XCTestCase {
    func testIndexKeepsContentAndNewsSourcesApart() {
        let index = KnowledgeSourceDerivatives.index(
            chats: [
                makeChat(id: 1, contentId: 7),
                makeChat(id: 2, contentId: 7, councilMode: true),
                makeChat(id: 3, newsItemId: 7)
            ],
            decks: [
                makeDeck(id: 4, sourceContentId: 7),
                makeDeck(id: 5, sourceKind: .githubRepo, sourceContentId: 8)
            ],
            narrations: [
                makeNarration(id: 6, contentIds: [8, 9], newsItemIds: [7])
            ]
        )

        XCTAssertEqual(index[KnowledgeSourceKey(id: 7, isNews: false)], [.chat, .council, .deck])
        XCTAssertEqual(index[KnowledgeSourceKey(id: 7, isNews: true)], [.chat, .narration])
        XCTAssertEqual(index[KnowledgeSourceKey(id: 8, isNews: false)], [.narration])
        XCTAssertEqual(index[KnowledgeSourceKey(id: 9, isNews: false)], [.narration])
    }

    func testSourceKeyUsesContentTypeNamespace() {
        XCTAssertEqual(
            KnowledgeSourceKey(.knowledgeSourceFixture(id: 7, contentType: .news)),
            KnowledgeSourceKey(id: 7, isNews: true)
        )
        XCTAssertEqual(
            KnowledgeSourceKey(.knowledgeSourceFixture(id: 7)),
            KnowledgeSourceKey(id: 7, isNews: false)
        )
    }

    private func makeChat(
        id: Int,
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        councilMode: Bool? = nil
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
            lastMessageRole: nil,
            councilMode: councilMode
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
            sourceNotesAvailable: false,
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

extension ContentSummary {
    /// A ready saved Knowledge source for source-action tests.
    static func knowledgeSourceFixture(
        id: Int,
        contentType: APIContentType = .article,
        title: String = "Saved source"
    ) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: "https://example.com/\(id)",
            title: title,
            source: "Example",
            platform: nil,
            status: .completed,
            shortSummary: "Summary",
            createdAt: "2026-09-27T12:00:00Z",
            processedAt: nil,
            classification: nil,
            publicationDate: nil,
            isRead: false,
            isSavedToKnowledge: true,
            knowledgeSavedAt: "2026-09-27T12:00:00Z"
        )
    }
}
