import XCTest
@testable import newsly

@MainActor
final class BriefingSourceActionViewModelTests: XCTestCase {
    func testBriefingKindPreservesNamespaceEvenForLegacyNewsContent() {
        XCTAssertEqual(BriefingSourceActionViewModel.knowledgeSource(for: source(kind: "news")), .news(7))
        XCTAssertEqual(BriefingSourceActionViewModel.knowledgeSource(for: source(kind: "content")), .content(7))
        XCTAssertNil(BriefingSourceActionViewModel.knowledgeSource(for: source(kind: "unknown")))
    }

    func testSavePreservesSameNumberedSourceNamespacesAndNeverToggles() async {
        var requests: [KnowledgeSource] = []
        let model = model(save: { source in
            requests.append(source)
            return requests.count == 1 ? "Saved" : "Already saved"
        })
        let first = await model.save(source: .news(7))
        let repeated = await model.save(source: .news(7))
        let content = await model.save(source: .content(7))
        XCTAssertEqual(first, "Saved")
        XCTAssertEqual(repeated, "Already saved")
        XCTAssertEqual(content, "Already saved")
        XCTAssertEqual(requests, [.news(7), .news(7), .content(7)])
        XCTAssertFalse(model.isBusy)
    }

    func testChatRoutesUseRequestedNamespaceAndFocusComposerWithoutSendingMessage() async {
        var requests: [KnowledgeSource] = []
        let model = model(chat: { source in
            requests.append(source)
            return ChatSessionRoute(
                sessionId: 8, contentId: source.contentId, newsItemId: source.newsItemId,
                focusComposerOnAppear: true
            )
        })
        let news = await model.startChat(source: .news(7))
        let content = await model.startChat(source: .content(7))
        XCTAssertEqual(requests, [.news(7), .content(7)])
        XCTAssertEqual(news?.newsItemId, 7)
        XCTAssertNil(news?.contentId)
        XCTAssertEqual(content?.contentId, 7)
        XCTAssertNil(content?.newsItemId)
        XCTAssertEqual(news?.focusComposerOnAppear, true)
        XCTAssertNil(news?.initialUserMessageText)
        XCTAssertNil(news?.pendingMessageId)
    }

    func testDeckReceivesNamespaceAndTrimmedOptionalFocus() async {
        var requests: [KnowledgeSource] = []
        var focuses: [String?] = []
        let model = model(deck: { source, focus in
            requests.append(source)
            focuses.append(focus)
            return self.deck()
        })
        let news = await model.createDeck(source: .news(7), focus: "  History \n")
        let content = await model.createDeck(source: .content(7), focus: " \n")
        XCTAssertEqual(news?.id, 9)
        XCTAssertEqual(content?.id, 9)
        XCTAssertEqual(requests, [.news(7), .content(7)])
        XCTAssertEqual(focuses, ["History", nil])
        XCTAssertFalse(model.isCreatingDeck)
    }

    func testResumedChatRetainsExistingSessionWithoutFocusingComposer() async {
        let existing = ChatSessionRoute(sessionId: 12, newsItemId: 7)
        let model = model(chat: { _ in existing })
        let route = await model.startChat(source: .news(7))
        XCTAssertEqual(route, existing)
        XCTAssertEqual(route?.focusComposerOnAppear, false)
    }

    func testExistingDeckLookupPreservesNamespaceWithoutCreating() async {
        var lookedUp: [KnowledgeSource] = []
        var createCalls = 0
        let model = model(
            deck: { _, _ in createCalls += 1; return self.deck() },
            findDeck: { source in lookedUp.append(source); return self.deck() }
        )
        let existing = await model.existingDeck(source: .news(7))
        XCTAssertEqual(existing?.id, 9)
        XCTAssertEqual(lookedUp, [.news(7)])
        XCTAssertEqual(createCalls, 0)
        XCTAssertFalse(model.isBusy)
        XCTAssertFalse(model.isCreatingDeck)
    }

    func testDeckLookupFailureReportsErrorInsteadOfCreating() async {
        let model = model(findDeck: { _ in throw TestFailure.failed })
        let existing = await model.existingDeck(source: .content(7))
        XCTAssertNil(existing)
        XCTAssertEqual(model.errorMessage, "Test failure")
        XCTAssertFalse(model.isBusy)
    }

    func testInFlightCommandRejectsDuplicateAndOtherCommands() async {
        var continuation: CheckedContinuation<String, Never>?
        var callCount = 0
        let model = model(save: { _ in
            callCount += 1
            return await withCheckedContinuation { continuation = $0 }
        })
        let first = Task { await model.save(source: .news(7)) }
        while continuation == nil { await Task.yield() }
        XCTAssertTrue(model.isSaving)
        let duplicate = await model.save(source: .news(7))
        let chat = await model.startChat(source: .content(7))
        let deck = await model.createDeck(source: .news(7), focus: nil)
        XCTAssertNil(duplicate)
        XCTAssertNil(chat)
        XCTAssertNil(deck)
        XCTAssertEqual(callCount, 1)
        continuation?.resume(returning: "Saved")
        let result = await first.value
        XCTAssertEqual(result, "Saved")
        XCTAssertFalse(model.isBusy)
    }

    func testFailureReleasesCommandAndSuccessfulRetryClearsError() async {
        var attempts = 0
        let model = model(save: { _ in
            attempts += 1
            if attempts == 1 { throw TestFailure.failed }
            return "Saved"
        })
        let failed = await model.save(source: .content(7))
        XCTAssertNil(failed)
        XCTAssertEqual(model.errorMessage, "Test failure")
        XCTAssertFalse(model.isSaving)
        let retry = await model.save(source: .content(7))
        XCTAssertEqual(retry, "Saved")
        XCTAssertNil(model.errorMessage)
    }

    func testCancellationSuppressesLateChatResult() async {
        var continuation: CheckedContinuation<ChatSessionRoute, Never>?
        let model = model(chat: { _ in
            await withCheckedContinuation { continuation = $0 }
        })
        let task = Task { await model.startChat(source: .news(7)) }
        while continuation == nil { await Task.yield() }
        task.cancel()
        continuation?.resume(returning: ChatSessionRoute(sessionId: 8))
        let result = await task.value
        XCTAssertNil(result)
        XCTAssertNil(model.errorMessage)
        XCTAssertFalse(model.isStartingChat)
    }

    func testCancellationSuppressesLateFailure() async {
        var continuation: CheckedContinuation<String, Error>?
        let model = model(save: { _ in
            try await withCheckedThrowingContinuation { continuation = $0 }
        })
        let task = Task { await model.save(source: .content(7)) }
        while continuation == nil { await Task.yield() }
        task.cancel()
        continuation?.resume(throwing: TestFailure.failed)
        let result = await task.value
        XCTAssertNil(result)
        XCTAssertNil(model.errorMessage)
        XCTAssertFalse(model.isBusy)
    }

    func testAlreadyCancelledTaskDoesNotSubmitCommand() async {
        var callCount = 0
        let model = model(save: { _ in callCount += 1; return "Saved" })
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return await model.save(source: .news(7))
        }
        let result = await task.value
        XCTAssertNil(result)
        XCTAssertEqual(callCount, 0)
    }

    private func model(
        save: @escaping (KnowledgeSource) async throws -> String = { _ in throw TestFailure.failed },
        chat: @escaping (KnowledgeSource) async throws -> ChatSessionRoute = { _ in throw TestFailure.failed },
        deck: @escaping (KnowledgeSource, String?) async throws -> LearningDeck = { _, _ in throw TestFailure.failed },
        findDeck: @escaping (KnowledgeSource) async throws -> LearningDeck? = { _ in nil }
    ) -> BriefingSourceActionViewModel {
        BriefingSourceActionViewModel(
            saveSource: save, startSourceChat: chat, findSourceDeck: { source, _ in try await findDeck(source) },
            createSourceDeck: deck
        )
    }

    private func source(kind: String) -> APIBriefingSource {
        APIBriefingSource(
            sourceKey: "\(kind):7", kind: kind, id: 7, title: "Source",
            publisher: nil, aggregator: nil, summary: nil, keyPoints: nil, url: nil, imageUrl: nil,
            thumbnailUrl: nil, publishedAt: nil, contentType: .news,
            durationSeconds: nil, readingMinutes: nil, discussion: nil
        )
    }

    private func deck() -> LearningDeck {
        LearningDeck(
            id: 9, title: "Deck", sourceKind: .content, sourceURL: nil,
            sourceContentId: 7, sourceTitle: "Source", sourceMetadata: [:],
            status: nil, shareEnabled: false, viewerAvailable: false,
            latestSuccessfulRunId: nil, latestRun: nil, createdAt: Date(), updatedAt: Date()
        )
    }
}

private enum TestFailure: LocalizedError {
    case failed
    var errorDescription: String? { "Test failure" }
}
