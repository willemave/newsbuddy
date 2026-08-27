import XCTest
@testable import newsly

@MainActor
final class KnowledgeTimelineViewModelTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_780_771_260)

    func testLoadsIndependentSourcesAndKeepsPartialSuccess() async {
        let saved = makeSaved(id: 11, activityDate: now)
        let store = makeStore(
            saved: [saved],
            chatLoadFails: true,
            decks: [makeDeck(id: 12, activityDate: now.addingTimeInterval(-60))],
            narrations: [makeNarration(id: 13, activityDate: now.addingTimeInterval(-120))]
        )

        await store.load()

        XCTAssertEqual(store.timeline.map(\.id), ["saved-11", "deck-12", "narration-13"])
        XCTAssertEqual(store.failures.map(\.id), ["chats.load"])

        await store.savedContent.toggleKnowledgeSave(saved.id)

        XCTAssertEqual(store.failures.map(\.id), ["saved.action", "chats.load"])
    }

    func testProjectionMaterializesWhenASourceChangesAndRepeatedReadsStayStable() async {
        let store = makeStore()
        let session = makeSession(id: 21, activityDate: now)

        store.chats.sessions = [session]
        await Task.yield()

        let expectedIDs = ["chat-21"]
        XCTAssertEqual(store.timeline.map(\.id), expectedIDs)
        XCTAssertEqual(store.groupedTimeline.flatMap(\.items).map(\.id), expectedIDs)
        for _ in 0..<5 {
            XCTAssertEqual(store.timeline.map(\.id), expectedIDs)
            XCTAssertEqual(store.groupedTimeline.flatMap(\.items).map(\.id), expectedIDs)
        }
    }

    func testInitialLoadPublishesOneMergedSnapshotAfterAllSourcesFinish() async {
        let saved = makeSaved(id: 31, activityDate: now)
        let narration = makeNarration(id: 32, activityDate: now.addingTimeInterval(-60))
        let store = makeStore(
            saved: [saved],
            narrations: [narration],
            savedLoadDelayNanoseconds: 500_000_000
        )

        XCTAssertTrue(store.isLoading)
        XCTAssertTrue(store.timeline.isEmpty)

        let load = Task { await store.load() }
        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(store.narrations.episodes.map(\.id), [narration.id])
        XCTAssertTrue(store.savedContent.contents.isEmpty)
        XCTAssertTrue(store.timeline.isEmpty)
        XCTAssertTrue(store.isLoading)

        await load.value

        XCTAssertEqual(store.timeline.map(\.id), ["saved-31", "narration-32"])
        XCTAssertFalse(store.isLoading)
    }

    func testCancelledInitialLoadStaysLoadingUntilAReloadCompletes() async {
        let saved = makeSaved(id: 41, activityDate: now)
        let store = makeStore(
            saved: [saved],
            savedLoadDelayNanoseconds: 500_000_000
        )

        let cancelledLoad = Task { await store.load() }
        try? await Task.sleep(nanoseconds: 100_000_000)
        cancelledLoad.cancel()
        await cancelledLoad.value

        XCTAssertTrue(store.isLoading)
        XCTAssertTrue(store.timeline.isEmpty)

        await store.load()

        XCTAssertEqual(store.timeline.map(\.id), ["saved-41"])
        XCTAssertFalse(store.isLoading)
    }

    private func makeStore(
        saved: [ContentSummary] = [],
        chatLoadFails: Bool = false,
        decks: [LearningDeck] = [],
        narrations: [AudioEpisode] = [],
        savedLoadDelayNanoseconds: UInt64 = 0
    ) -> KnowledgeTimelineViewModel {
        KnowledgeTimelineViewModel(
            savedContent: ContentListViewModel(
                contentService: KnowledgeTimelineContentService(
                    contents: saved,
                    loadDelayNanoseconds: savedLoadDelayNanoseconds
                )
            ),
            chats: KnowledgeChatViewModel(
                chatService: KnowledgeTimelineChatService(loadFails: chatLoadFails)
            ),
            decks: LearningDecksViewModel(
                service: KnowledgeTimelineDeckService(decks: decks)
            ),
            narrations: CustomNarrationLibraryViewModel(
                playbackService: .shared,
                audioService: KnowledgeTimelineNarrationService(episodes: narrations),
                badgeStatsStore: .shared,
                toastPresenter: ToastService.shared
            )
        )
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
            lastMessagePreview: "A materialized preview",
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

    private func makeDeck(id: Int, activityDate: Date) -> LearningDeck {
        LearningDeck(
            id: id,
            title: "Deck \(id)",
            sourceKind: .content,
            sourceURL: nil,
            sourceContentId: nil,
            sourceTitle: nil,
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
private final class KnowledgeTimelineChatService: KnowledgeChatServicing {
    private enum Failure: Error { case unavailable }
    private let loadFails: Bool

    init(loadFails: Bool) {
        self.loadFails = loadFails
    }

    func listSessionsPage(
        contentId: Int?,
        newsItemId: Int?,
        limit: Int,
        cursor: String?
    ) async throws -> ChatSessionListResponse {
        if loadFails { throw Failure.unavailable }
        return ChatSessionListResponse(
            sessions: [],
            meta: PaginationMetadata(hasMore: false, pageSize: 0)
        )
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

@MainActor
private final class KnowledgeTimelineContentService: ContentSummaryListServicing {
    private enum Failure: Error { case unused }
    let contents: [ContentSummary]
    let loadDelayNanoseconds: UInt64

    init(contents: [ContentSummary], loadDelayNanoseconds: UInt64) {
        self.contents = contents
        self.loadDelayNanoseconds = loadDelayNanoseconds
    }

    func fetchKnowledgeLibrary(
        query: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse {
        try await Task.sleep(nanoseconds: loadDelayNanoseconds)
        return ContentListResponse(
            contents: contents,
            availableDates: [],
            contentTypes: [],
            meta: PaginationMetadata(hasMore: false, pageSize: contents.count)
        )
    }

    func fetchRecentlyReadList(
        contentType: String?,
        date: String?,
        cursor: String?,
        limit: Int
    ) async throws -> ContentListResponse { throw Failure.unused }

    func saveToKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw Failure.unused
    }

    func removeFromKnowledge(id: Int) async throws -> KnowledgeMutationResponse {
        throw Failure.unused
    }

    func downloadMoreFromSeries(
        contentId: Int,
        count: Int
    ) async throws -> DownloadMoreResponse { throw Failure.unused }

    func markContentAsUnread(id: Int) async throws { throw Failure.unused }
}

@MainActor
private final class KnowledgeTimelineDeckService: LearningDeckServicing {
    private enum Failure: Error { case unused }
    let decks: [LearningDeck]

    init(decks: [LearningDeck]) {
        self.decks = decks
    }

    func listDecks() async throws -> LearningDeckListResponse {
        LearningDeckListResponse(decks: decks)
    }

    func fetchDeck(id: Int) async throws -> LearningDeck { throw Failure.unused }

    func createDeck(
        contentId: Int?,
        newsItemId: Int?,
        url: String?,
        interestsPrompt: String?
    ) async throws -> LearningDeck { throw Failure.unused }

    func retryDeck(deckId: Int) async throws -> LearningDeck { throw Failure.unused }
    func viewerURL(deckId: Int) async throws -> URL { throw Failure.unused }
    func sourceNotesURL(deckId: Int) async throws -> URL { throw Failure.unused }
    func enableShare(deckId: Int) async throws -> LearningDeckShareResponse {
        throw Failure.unused
    }
    func disableShare(deckId: Int) async throws -> LearningDeckShareResponse {
        throw Failure.unused
    }
    func deleteDeck(deckId: Int) async throws { throw Failure.unused }
}

@MainActor
private final class KnowledgeTimelineNarrationService: CustomNarrationLibraryServicing {
    private enum Failure: Error { case unused }
    let episodes: [AudioEpisode]

    init(episodes: [AudioEpisode]) {
        self.episodes = episodes
    }

    func createCustomNarrationEpisode(
        contentIds: [Int],
        newsItemIds: [Int],
        title: String?,
        markSourceContentReadOnPlay: Bool,
        delivery: AudioEpisodeDelivery
    ) async throws -> AudioEpisode { throw Failure.unused }

    func fetchEpisode(id: Int) async throws -> AudioEpisode { throw Failure.unused }

    func fetchCustomNarrationEpisodes(limit: Int) async throws -> [AudioEpisode] {
        episodes
    }

    func streamResource(
        for episode: AudioEpisode
    ) async throws -> AuthorizedMediaResource { throw Failure.unused }

    func enableEpisodeShare(id: Int) async throws -> AudioEpisodeShareResponse {
        throw Failure.unused
    }
}
