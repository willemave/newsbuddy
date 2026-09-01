import XCTest
@testable import newsly

@MainActor
final class KnowledgeTimelineItemTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_780_771_260)

    func testMergedTimelineSortsNewestFirstAcrossSources() {
        let chat = makeSession(id: 1, activityDate: now.addingTimeInterval(-12 * 60))
        let deck = makeDeck(id: 2, activityDate: now.addingTimeInterval(-42 * 60))
        let narration = makeNarration(id: 3, activityDate: now.addingTimeInterval(-25 * 60 * 60))

        let items = KnowledgeTimelineItem.merged(
            saved: [],
            chats: [chat],
            decks: [deck],
            narrations: [narration]
        )

        XCTAssertEqual(items.map(\.id), ["chat-1", "deck-2", "narration-3"])
    }

    func testStableIDsAreNamespacedByItemKind() {
        let activityDate = now.addingTimeInterval(-60)
        let items = KnowledgeTimelineItem.merged(
            saved: [],
            chats: [makeSession(id: 7, activityDate: activityDate)],
            decks: [makeDeck(id: 7, activityDate: activityDate)],
            narrations: [makeNarration(id: 7, activityDate: activityDate)]
        )

        XCTAssertEqual(Set(items.map(\.id)), ["chat-7", "deck-7", "narration-7"])
    }

    func testSavedItemsJoinTheSameReverseChronologicalTimeline() {
        let saved = makeSaved(id: 9, activityDate: now.addingTimeInterval(-5 * 60))
        let chat = makeSession(id: 9, activityDate: now.addingTimeInterval(-10 * 60))

        let items = KnowledgeTimelineItem.merged(
            saved: [saved],
            chats: [chat],
            decks: [],
            narrations: []
        )

        XCTAssertEqual(items.map(\.id), ["saved-9", "chat-9"])
    }

    func testPaginationLoadsTheSourceWhoseOldestItemIsNewest() {
        XCTAssertEqual(
            KnowledgePaginationSource.next(
                savedOldest: now.addingTimeInterval(-60),
                chatOldest: now.addingTimeInterval(-120)
            ),
            .saved
        )
        XCTAssertEqual(
            KnowledgePaginationSource.next(savedOldest: nil, chatOldest: now),
            .chats
        )
        XCTAssertNil(KnowledgePaginationSource.next(savedOldest: nil, chatOldest: nil))
    }

    func testMergePrecomputesSingleLineChatPreview() {
        let items = KnowledgeTimelineItem.merged(
            saved: [],
            chats: [
                makeSession(
                    id: 8,
                    activityDate: now,
                    lastMessagePreview: "## **Signal**\n- First point\n- Second point"
                )
            ],
            decks: [],
            narrations: []
        )

        guard case .chat(let session, let preview) = items.first else {
            return XCTFail("Expected a chat timeline item")
        }
        XCTAssertEqual(session.id, 8)
        XCTAssertEqual(preview, "Signal First point Second point")
    }

    func testDeckSubtitleSuppressesRepeatedSourceTitle() {
        let deck = makeDeck(
            id: 5,
            activityDate: now,
            title: "Mini NAS Showdown",
            sourceTitle: "Mini NAS Showdown"
        )

        XCTAssertEqual(deck.timelineSubtitle, "Interactive lesson")
    }

    private func makeSession(
        id: Int,
        activityDate: Date,
        lastMessagePreview: String? = nil
    ) -> ChatSessionSummary {
        ChatSessionSummary(
            id: id,
            contentId: nil,
            title: "Chat \(id)",
            sessionType: "knowledge_chat",
            topic: nil,
            llmProvider: "openai",
            llmModel: "openai:gpt-5.5",
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate,
            lastMessageAt: activityDate,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: false,
            isSavedToKnowledge: false,
            hasMessages: true,
            lastMessagePreview: lastMessagePreview,
            lastMessageRole: nil
        )
    }

    private func makeSaved(id: Int, activityDate: Date) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: .article,
            url: "https://example.com/\(id)",
            title: "Saved \(id)",
            source: "Example",
            platform: nil,
            status: .completed,
            shortSummary: "Summary",
            createdAt: ServerDate.format(activityDate.addingTimeInterval(-365 * 24 * 60 * 60)),
            processedAt: nil,
            classification: nil,
            publicationDate: ServerDate.format(
                activityDate.addingTimeInterval(-365 * 24 * 60 * 60)
            ),
            isRead: false,
            isSavedToKnowledge: true,
            knowledgeSavedAt: ServerDate.format(activityDate)
        )
    }

    private func makeDeck(
        id: Int,
        activityDate: Date,
        title: String = "Deck",
        sourceTitle: String? = nil
    ) -> LearningDeck {
        LearningDeck(
            id: id,
            title: title,
            sourceKind: .content,
            sourceURL: nil,
            sourceContentId: nil,
            sourceTitle: sourceTitle,
            sourceMetadata: [:],
            status: .completed,
            shareEnabled: false,
            viewerAvailable: true,
            sourceNotesAvailable: false,
            latestSuccessfulRunId: nil,
            latestRun: nil,
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate
        )
    }

    private func makeNarration(id: Int, activityDate: Date) -> AudioEpisode {
        AudioEpisode(
            id: id,
            kind: .custom_narration,
            status: .completed,
            title: "Narration \(id)",
            sourceContentId: nil,
            subtitle: nil,
            artworkUrl: nil,
            durationSeconds: nil,
            audioUrl: nil,
            streamUrl: nil,
            scriptText: nil,
            errorMessage: nil,
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate
        )
    }
}
