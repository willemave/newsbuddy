import XCTest
@testable import newsly

@MainActor
final class LearningTimelineItemTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_780_771_260)

    func testMergedTimelineSortsNewestFirstAcrossSources() {
        let chat = makeSession(id: 1, activityDate: now.addingTimeInterval(-12 * 60))
        let deck = makeDeck(id: 2, activityDate: now.addingTimeInterval(-42 * 60))
        let narration = makeNarration(id: 3, activityDate: now.addingTimeInterval(-25 * 60 * 60))

        let items = LearningTimelineItem.merged(
            chats: [chat],
            decks: [deck],
            narrations: [narration]
        )

        XCTAssertEqual(items.map(\.id), ["chat-1", "deck-2", "narration-3"])
    }

    func testStableIDsAreNamespacedByItemKind() {
        let activityDate = now.addingTimeInterval(-60)
        let items = LearningTimelineItem.merged(
            chats: [makeSession(id: 7, activityDate: activityDate)],
            decks: [makeDeck(id: 7, activityDate: activityDate)],
            narrations: [makeNarration(id: 7, activityDate: activityDate)]
        )

        XCTAssertEqual(Set(items.map(\.id)), ["chat-7", "deck-7", "narration-7"])
    }

    func testMergeKeepsSuccessfulSourcesWhenChatSourceFails() async {
        let chatViewModel = LearningHubViewModel(chatService: FailingLearningTimelineChatService())
        await chatViewModel.loadLearning()

        let items = LearningTimelineItem.merged(
            chats: chatViewModel.sessions,
            decks: [makeDeck(id: 4, activityDate: now)],
            narrations: [makeNarration(id: 5, activityDate: now.addingTimeInterval(-60))]
        )

        XCTAssertNotNil(chatViewModel.errorMessage)
        XCTAssertEqual(items.map(\.id), ["deck-4", "narration-5"])
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

    private func makeSession(id: Int, activityDate: Date) -> ChatSessionSummary {
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
            lastMessagePreview: nil,
            lastMessageRole: nil
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
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate
        )
    }
}

@MainActor
private final class FailingLearningTimelineChatService: LearningHubChatServicing {
    private enum Failure: Error {
        case unavailable
    }

    func listSessionsPage(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int,
        cursor: String?
    ) async throws -> ChatSessionListResponse {
        throw Failure.unavailable
    }

    func createAssistantTurn(
        message: String,
        sessionId: Int?,
        screenContext: AssistantScreenContext
    ) async throws -> AssistantTurnResponse {
        throw Failure.unavailable
    }

    func deleteSession(sessionId: Int) async throws {
        throw Failure.unavailable
    }
}
