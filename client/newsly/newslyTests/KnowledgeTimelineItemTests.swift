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

    func testSavedSourceCoalescesGeneratingDeckAndActiveChatIntoOneStableRow() throws {
        let savedDate = now.addingTimeInterval(-30 * 60)
        let chatDate = now.addingTimeInterval(-10 * 60)
        let deckDate = now.addingTimeInterval(-5 * 60)
        let items = KnowledgeTimelineItem.merged(
            saved: [makeSaved(id: 21, activityDate: savedDate)],
            chats: [
                makeSession(
                    id: 31,
                    activityDate: chatDate,
                    contentId: 21,
                    hasPendingMessage: true
                )
            ],
            decks: [
                makeDeck(
                    id: 41,
                    activityDate: deckDate,
                    sourceContentId: 21,
                    runStatus: .generating
                )
            ],
            narrations: []
        )

        let item = try XCTUnwrap(items.first)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(item.id, "saved-21")
        XCTAssertEqual(item.activityDate, deckDate)
        guard case .saved(let entry) = item else {
            return XCTFail("Expected the saved source row")
        }
        XCTAssertEqual(entry.content.id, 21)
        XCTAssertEqual(entry.chats.map(\.id), [31])
        XCTAssertEqual(entry.decks.map(\.id), [41])
        XCTAssertEqual(entry.statusText, "Deck generating · Chat answering")
        XCTAssertTrue(entry.isBusy)
    }

    func testSavedSourceCoalescesCompletedArtifactsWithoutBusyIndicator() throws {
        let items = KnowledgeTimelineItem.merged(
            saved: [makeSaved(id: 22, activityDate: now.addingTimeInterval(-30 * 60))],
            chats: [
                makeSession(id: 32, activityDate: now, contentId: 22)
            ],
            decks: [
                makeDeck(
                    id: 42,
                    activityDate: now.addingTimeInterval(-5 * 60),
                    sourceContentId: 22,
                    runStatus: .completed
                )
            ],
            narrations: []
        )

        let item = try XCTUnwrap(items.first)
        XCTAssertEqual(items.count, 1)
        XCTAssertEqual(item.id, "saved-22")
        XCTAssertEqual(item.activityDate, now)
        guard case .saved(let entry) = item else {
            return XCTFail("Expected the saved source row")
        }
        XCTAssertEqual(entry.statusText, "Deck ready")
        XCTAssertFalse(entry.isBusy, "Terminal deck and chat state must not show a spinner")
    }

    func testSavedRowIDRemainsStableAsLinkedArtifactsFinish() {
        let saved = makeSaved(id: 20, activityDate: now.addingTimeInterval(-30 * 60))
        let generating = KnowledgeTimelineItem.merged(
            saved: [saved],
            chats: [
                makeSession(
                    id: 30,
                    activityDate: now,
                    contentId: 20,
                    hasPendingMessage: true
                )
            ],
            decks: [
                makeDeck(id: 40, activityDate: now, sourceContentId: 20, runStatus: .generating)
            ],
            narrations: []
        )
        let completed = KnowledgeTimelineItem.merged(
            saved: [saved],
            chats: [makeSession(id: 30, activityDate: now, contentId: 20)],
            decks: [
                makeDeck(id: 40, activityDate: now, sourceContentId: 20, runStatus: .completed)
            ],
            narrations: []
        )

        XCTAssertEqual(generating.map(\.id), ["saved-20"])
        XCTAssertEqual(completed.map(\.id), generating.map(\.id))
    }

    func testMergeRetainsArtifactsThatDoNotBelongToALoadedSavedSource() {
        let items = KnowledgeTimelineItem.merged(
            saved: [makeSaved(id: 30, activityDate: now.addingTimeInterval(-120))],
            chats: [
                makeSession(
                    id: 81,
                    activityDate: now,
                    articleTitle: "Saved 30",
                    articleUrl: "https://example.com/30"
                )
            ],
            decks: [
                makeDeck(
                    id: 82,
                    activityDate: now.addingTimeInterval(-60),
                    sourceTitle: "Saved 30",
                    sourceURL: "https://example.com/30"
                )
            ],
            narrations: []
        )

        XCTAssertEqual(items.map(\.id), ["chat-81", "deck-82", "saved-30"])
    }

    func testSavedEntryActivityUsesNewestSourceChatOrDeckDateForTimelineOrdering() throws {
        let saved = makeSaved(id: 23, activityDate: now.addingTimeInterval(-30 * 60))
        let chat = makeSession(
            id: 33,
            activityDate: now.addingTimeInterval(-2 * 60),
            contentId: 23
        )
        let deck = makeDeck(
            id: 43,
            activityDate: now.addingTimeInterval(-10 * 60),
            sourceContentId: 23
        )
        let items = KnowledgeTimelineItem.merged(
            saved: [saved],
            chats: [chat],
            decks: [deck],
            narrations: [makeNarration(id: 44, activityDate: now.addingTimeInterval(-5 * 60))]
        )

        XCTAssertEqual(items.map(\.id), ["saved-23", "narration-44"])
        guard case .saved(let entry) = try XCTUnwrap(items.first) else {
            return XCTFail("Expected the saved source row")
        }
        XCTAssertEqual(entry.activityDate, chat.lastActivityDate)
    }

    func testSavedEntryRetainsAllChatsNewestFirstWithDeterministicTies() throws {
        let tiedDate = now.addingTimeInterval(-5 * 60)
        let items = KnowledgeTimelineItem.merged(
            saved: [makeSaved(id: 24, activityDate: now.addingTimeInterval(-60 * 60))],
            chats: [
                makeSession(id: 53, activityDate: tiedDate, contentId: 24),
                makeSession(id: 51, activityDate: now, contentId: 24),
                makeSession(id: 52, activityDate: tiedDate, contentId: 24)
            ],
            decks: [],
            narrations: []
        )

        guard case .saved(let entry) = try XCTUnwrap(items.first) else {
            return XCTFail("Expected the saved source row")
        }
        XCTAssertEqual(entry.chats.map(\.id), [51, 52, 53])
    }

    func testContentAndNewsIDsRemainSeparateSourceNamespaces() throws {
        let items = KnowledgeTimelineItem.merged(
            saved: [
                makeSaved(id: 25, activityDate: now, contentType: .article),
                makeSaved(id: 25, activityDate: now.addingTimeInterval(-60), contentType: .news)
            ],
            chats: [
                makeSession(id: 61, activityDate: now, contentId: 25),
                makeSession(id: 62, activityDate: now, newsItemId: 25)
            ],
            decks: [],
            narrations: []
        )

        XCTAssertEqual(items.count, 2)
        let savedEntries = items.compactMap { item -> KnowledgeSavedTimelineEntry? in
            guard case .saved(let entry) = item else { return nil }
            return entry
        }
        let contentEntry = try XCTUnwrap(savedEntries.first { $0.content.contentType == .article })
        let newsEntry = try XCTUnwrap(savedEntries.first { $0.content.contentType == .news })
        XCTAssertEqual(Set(items.map(\.id)), ["saved-25", "saved-news-25"])
        XCTAssertEqual(contentEntry.chats.map(\.id), [61])
        XCTAssertEqual(newsEntry.chats.map(\.id), [62])
    }

    func testArtifactsReturnToStandaloneRowsWhenSavedSourceIsRemovedOrNotLoaded() {
        let chat = makeSession(id: 71, activityDate: now, contentId: 26)
        let deck = makeDeck(
            id: 72,
            activityDate: now.addingTimeInterval(-60),
            sourceContentId: 26
        )

        let coalesced = KnowledgeTimelineItem.merged(
            saved: [makeSaved(id: 26, activityDate: now.addingTimeInterval(-120))],
            chats: [chat],
            decks: [deck],
            narrations: []
        )
        let standalone = KnowledgeTimelineItem.merged(
            saved: [],
            chats: [chat],
            decks: [deck],
            narrations: []
        )

        XCTAssertEqual(coalesced.map(\.id), ["saved-26"])
        XCTAssertEqual(standalone.map(\.id), ["chat-71", "deck-72"])
    }

    func testSavedEntryBusyStateIncludesSourceDeckAndChatActivity() {
        let freshDate = Date()
        let processingSource = KnowledgeSavedTimelineEntry(
            content: makeSaved(id: 27, activityDate: freshDate, status: .processing)
        )
        let activeDeck = KnowledgeSavedTimelineEntry(
            content: makeSaved(id: 28, activityDate: now),
            decks: [
                makeDeck(id: 73, activityDate: now, sourceContentId: 28, runStatus: .generating)
            ]
        )
        let preparingChat = KnowledgeSavedTimelineEntry(
            content: makeSaved(id: 29, activityDate: now),
            chats: [
                makeSession(
                    id: 74,
                    activityDate: now,
                    contentId: 29,
                    isWaitingForContent: true
                )
            ]
        )

        XCTAssertTrue(processingSource.isBusy)
        XCTAssertTrue(activeDeck.isBusy)
        XCTAssertEqual(activeDeck.statusText, "Deck generating")
        XCTAssertTrue(preparingChat.isBusy)
        XCTAssertEqual(preparingChat.statusText, "Chat preparing")
    }

    func testLatestRunUpdateControlsStandaloneAndGroupedDeckActivityOrdering() throws {
        let deckUpdate = now.addingTimeInterval(-20 * 60)
        let runUpdate = now.addingTimeInterval(-2 * 60)
        let narrationDate = now.addingTimeInterval(-10 * 60)
        let deck = makeDeck(
            id: 75,
            activityDate: deckUpdate,
            sourceContentId: 31,
            runStatus: .completed,
            runDates: (created: deckUpdate.addingTimeInterval(-60), updated: runUpdate)
        )
        let narration = makeNarration(id: 76, activityDate: narrationDate)

        let standalone = KnowledgeTimelineItem.merged(
            saved: [],
            chats: [],
            decks: [deck],
            narrations: [narration]
        )
        let grouped = KnowledgeTimelineItem.merged(
            saved: [makeSaved(id: 31, activityDate: now.addingTimeInterval(-30 * 60))],
            chats: [],
            decks: [deck],
            narrations: [narration]
        )

        XCTAssertEqual(standalone.map(\.id), ["deck-75", "narration-76"])
        XCTAssertEqual(grouped.map(\.id), ["saved-31", "narration-76"])
        guard case .saved(let entry) = try XCTUnwrap(grouped.first) else {
            return XCTFail("Expected the saved source row")
        }
        XCTAssertEqual(entry.activityDate, runUpdate)
    }

    func testDeckActivityUsesNewestAvailableCreatedOrUpdatedDate() {
        let newestDeckCreatedAt = now.addingTimeInterval(-2 * 60)
        let deckCreatedNewest = makeDeck(
            id: 77,
            activityDate: now.addingTimeInterval(-20 * 60),
            runStatus: .completed,
            deckDates: (
                created: newestDeckCreatedAt,
                updated: now.addingTimeInterval(-10 * 60)
            ),
            runDates: (
                created: now.addingTimeInterval(-30 * 60),
                updated: now.addingTimeInterval(-40 * 60)
            )
        )
        let newestRunCreatedAt = now.addingTimeInterval(-1 * 60)
        let nilUpdates = makeDeck(
            id: 78,
            activityDate: now.addingTimeInterval(-20 * 60),
            runStatus: .completed,
            deckDates: (created: now.addingTimeInterval(-30 * 60), updated: nil),
            runDates: (created: newestRunCreatedAt, updated: nil)
        )

        XCTAssertEqual(KnowledgeTimelineItem.deck(deckCreatedNewest).activityDate, newestDeckCreatedAt)
        XCTAssertEqual(KnowledgeTimelineItem.deck(nilUpdates).activityDate, newestRunCreatedAt)
    }

    func testBusyDeckStatusWinsOverNewerReadyDeck() {
        let entry = KnowledgeSavedTimelineEntry(
            content: makeSaved(id: 32, activityDate: now.addingTimeInterval(-30 * 60)),
            decks: [
                makeDeck(
                    id: 79,
                    activityDate: now,
                    sourceContentId: 32,
                    runStatus: .completed
                ),
                makeDeck(
                    id: 80,
                    activityDate: now.addingTimeInterval(-5 * 60),
                    sourceContentId: 32,
                    runStatus: .generating
                )
            ]
        )

        XCTAssertEqual(entry.statusText, "Deck generating")
        XCTAssertTrue(entry.isBusy)
    }

    func testFreshFailedAndCancelledDecksUseTerminalStatusWithoutSpinner() {
        let failed = KnowledgeSavedTimelineEntry(
            content: makeSaved(id: 33, activityDate: now),
            decks: [makeDeck(id: 81, activityDate: now, runStatus: .failed)]
        )
        let cancelled = KnowledgeSavedTimelineEntry(
            content: makeSaved(id: 34, activityDate: now),
            decks: [makeDeck(id: 82, activityDate: now, runStatus: .cancelled)]
        )

        XCTAssertEqual(failed.statusText, "Deck failed")
        XCTAssertFalse(failed.isBusy)
        XCTAssertEqual(cancelled.statusText, "Deck cancelled")
        XCTAssertFalse(cancelled.isBusy)
    }

    func testFailedRerunKeepsPriorSuccessfulDeckReadyWithoutSpinner() {
        let entry = KnowledgeSavedTimelineEntry(
            content: makeSaved(id: 35, activityDate: now),
            decks: [
                makeDeck(
                    id: 83,
                    activityDate: now,
                    runStatus: .failed,
                    hasPriorSuccessfulRun: true
                )
            ]
        )

        XCTAssertEqual(entry.statusText, "Deck ready")
        XCTAssertFalse(entry.isBusy)
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
        contentId: Int? = nil,
        newsItemId: Int? = nil,
        hasPendingMessage: Bool = false,
        isWaitingForContent: Bool = false,
        articleTitle: String? = nil,
        articleUrl: String? = nil,
        lastMessagePreview: String? = nil
    ) -> ChatSessionSummary {
        .fixture(
            id: id,
            contentId: contentId,
            newsItemId: newsItemId,
            title: "Chat \(id)",
            createdAt: activityDate.addingTimeInterval(-60),
            updatedAt: activityDate,
            lastMessageAt: activityDate,
            articleTitle: articleTitle,
            articleUrl: articleUrl,
            hasPendingMessage: hasPendingMessage,
            isWaitingForContent: isWaitingForContent,
            lastMessagePreview: lastMessagePreview
        )
    }

    private func makeSaved(
        id: Int,
        activityDate: Date,
        contentType: APIContentType = .article,
        status: APIContentStatus = .completed
    ) -> ContentSummary {
        ContentSummary(
            id: id,
            contentType: contentType,
            url: "https://example.com/\(id)",
            title: "Saved \(id)",
            source: "Example",
            platform: nil,
            status: status,
            shortSummary: "Summary",
            createdAt: ServerDate.format(activityDate.addingTimeInterval(-60)),
            processedAt: nil,
            isRead: false,
            isSavedToKnowledge: true,
            knowledgeSavedAt: ServerDate.format(activityDate)
        )
    }

    private func makeDeck(
        id: Int,
        activityDate: Date,
        title: String = "Deck",
        sourceTitle: String? = nil,
        sourceURL: String? = nil,
        sourceContentId: Int? = nil,
        runStatus: LearningDeckRunStatus? = nil,
        hasPriorSuccessfulRun: Bool = false,
        deckDates: (created: Date, updated: Date?)? = nil,
        runDates: (created: Date, updated: Date?)? = nil
    ) -> LearningDeck {
        let resolvedDeckDates = deckDates ?? (
            created: activityDate.addingTimeInterval(-60),
            updated: activityDate
        )
        let resolvedRunDates = runDates ?? (
            created: activityDate.addingTimeInterval(-60),
            updated: activityDate
        )
        let latestRun = runStatus.map { status in
            LearningDeckRun(
                id: id * 10,
                status: status,
                interestsPrompt: nil,
                timeline: [],
                errorMessage: nil,
                startedAt: activityDate.addingTimeInterval(-60),
                completedAt: status.isActive ? nil : activityDate,
                createdAt: resolvedRunDates.created,
                updatedAt: resolvedRunDates.updated
            )
        }
        let currentRunSucceeded = runStatus == .completed || runStatus == .ready || runStatus == nil
        let viewerAvailable = currentRunSucceeded || hasPriorSuccessfulRun
        return LearningDeck(
            id: id,
            title: title,
            sourceKind: .content,
            sourceURL: sourceURL,
            sourceContentId: sourceContentId,
            sourceTitle: sourceTitle,
            sourceMetadata: [:],
            status: runStatus ?? .completed,
            shareEnabled: false,
            viewerAvailable: viewerAvailable,
            latestSuccessfulRunId: viewerAvailable ? id * 10 - (currentRunSucceeded ? 0 : 1) : nil,
            latestRun: latestRun,
            createdAt: resolvedDeckDates.created,
            updatedAt: resolvedDeckDates.updated
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
