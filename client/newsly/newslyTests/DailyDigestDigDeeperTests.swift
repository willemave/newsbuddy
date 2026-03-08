//
//  DailyDigestDigDeeperTests.swift
//  newslyTests
//

import Combine
import Foundation
import XCTest
@testable import newsly

final class DailyDigestDigDeeperTests: XCTestCase {
    func testStartDailyDigestChatResponseDecodes() throws {
        let data = Data(
            """
            {
              "session": {
                "id": 42,
                "content_id": null,
                "title": "Daily AI Digest",
                "session_type": "daily_digest_brain",
                "topic": null,
                "llm_provider": "anthropic",
                "llm_model": "anthropic:claude-opus-4-5-20251101",
                "created_at": "2026-03-08T18:00:00Z",
                "updated_at": null,
                "last_message_at": null,
                "is_archived": false,
                "article_title": null,
                "article_url": null,
                "article_summary": null,
                "article_source": null,
                "has_pending_message": true,
                "is_favorite": false,
                "has_messages": true,
                "last_message_preview": null,
                "last_message_role": null
              },
              "user_message": {
                "id": 99,
                "session_id": 42,
                "role": "user",
                "content": "Dig deeper into these digest bullets.",
                "timestamp": "2026-03-08T18:00:00Z",
                "status": "processing",
                "error": null
              },
              "message_id": 99,
              "status": "processing"
            }
            """.utf8
        )

        let response = try JSONDecoder().decode(StartDailyDigestChatResponse.self, from: data)

        XCTAssertEqual(response.session.id, 42)
        XCTAssertEqual(response.session.sessionType, "daily_digest_brain")
        XCTAssertEqual(response.messageId, 99)
        XCTAssertEqual(response.status, .processing)
        XCTAssertEqual(response.userMessage.status, .processing)
    }

    func testChatSessionSummaryUsesDailyDigestPresentation() {
        let session = ChatSessionSummary(
            id: 42,
            contentId: nil,
            title: "Daily AI Digest",
            sessionType: "daily_digest_brain",
            topic: nil,
            llmProvider: "anthropic",
            llmModel: "anthropic:claude-opus-4-5-20251101",
            createdAt: "2026-03-08T18:00:00Z",
            updatedAt: nil,
            lastMessageAt: nil,
            articleTitle: nil,
            articleUrl: nil,
            articleSummary: nil,
            articleSource: nil,
            hasPendingMessage: true,
            isFavorite: false,
            hasMessages: true,
            lastMessagePreview: nil,
            lastMessageRole: nil
        )

        XCTAssertEqual(session.displaySubtitle, "About your daily digest")
        XCTAssertEqual(session.sessionTypeIconName, "calendar.badge.clock")
        XCTAssertEqual(session.sessionTypeLabel, "Daily Digest")
    }

    @MainActor
    func testDailyDigestListViewModelStartsDigDeeperChatAndTracksLoading() async throws {
        let repository = FakeDailyNewsDigestRepository(
            startResult: .success(makeStartResponse(sessionId: 42))
        )
        let viewModel = DailyDigestListViewModel(
            repository: repository,
            unreadCountService: .shared
        )

        let task = Task { try await viewModel.startDigDeeperChat(id: 7) }
        await Task.yield()

        XCTAssertTrue(viewModel.isStartingDigDeeperChat(for: 7))

        let route = try await task.value

        XCTAssertEqual(repository.startedIds, [7])
        XCTAssertEqual(route.sessionId, 42)
        XCTAssertFalse(viewModel.isStartingDigDeeperChat(for: 7))
        XCTAssertNil(viewModel.digDeeperError(for: 7))
    }

    @MainActor
    func testDailyDigestListViewModelStoresDigDeeperErrorPerDigest() async {
        let repository = FakeDailyNewsDigestRepository(startResult: .failure(FakeRepositoryError.boom))
        let viewModel = DailyDigestListViewModel(
            repository: repository,
            unreadCountService: .shared
        )

        do {
            _ = try await viewModel.startDigDeeperChat(id: 11)
            XCTFail("Expected dig deeper chat start to fail")
        } catch {
            XCTAssertEqual(error.localizedDescription, "Boom")
        }

        XCTAssertEqual(viewModel.digDeeperError(for: 11), "Boom")
        XCTAssertFalse(viewModel.isStartingDigDeeperChat(for: 11))
    }

    private func makeStartResponse(sessionId: Int) -> StartDailyDigestChatResponse {
        StartDailyDigestChatResponse(
            session: ChatSessionSummary(
                id: sessionId,
                contentId: nil,
                title: "Daily AI Digest",
                sessionType: "daily_digest_brain",
                topic: nil,
                llmProvider: "anthropic",
                llmModel: "anthropic:claude-opus-4-5-20251101",
                createdAt: "2026-03-08T18:00:00Z",
                updatedAt: nil,
                lastMessageAt: nil,
                articleTitle: nil,
                articleUrl: nil,
                articleSummary: nil,
                articleSource: nil,
                hasPendingMessage: true,
                isFavorite: false,
                hasMessages: true,
                lastMessagePreview: nil,
                lastMessageRole: nil
            ),
            userMessage: ChatMessage(
                id: 99,
                role: .user,
                timestamp: "2026-03-08T18:00:00Z",
                content: "Dig deeper into these digest bullets.",
                status: .processing
            ),
            messageId: 99,
            status: .processing
        )
    }
}

private enum FakeRepositoryError: LocalizedError {
    case boom

    var errorDescription: String? {
        "Boom"
    }
}

private final class FakeDailyNewsDigestRepository: DailyNewsDigestRepositoryType {
    var startedIds: [Int] = []
    let startResult: Result<StartDailyDigestChatResponse, Error>

    init(startResult: Result<StartDailyDigestChatResponse, Error>) {
        self.startResult = startResult
    }

    func loadPage(
        readFilter: ReadFilter,
        cursor: String?,
        limit: Int?
    ) -> AnyPublisher<DailyNewsDigestListResponse, Error> {
        fatalError("unused in test")
    }

    func markRead(id: Int) -> AnyPublisher<Void, Error> {
        fatalError("unused in test")
    }

    func markUnread(id: Int) -> AnyPublisher<Void, Error> {
        fatalError("unused in test")
    }

    func fetchVoiceSummaryAudio(id: Int) async throws -> Data {
        fatalError("unused in test")
    }

    func fetchVoiceSummary(id: Int) async throws -> DailyNewsDigestVoiceSummaryResponse {
        fatalError("unused in test")
    }

    func startDigDeeperChat(id: Int) async throws -> StartDailyDigestChatResponse {
        startedIds.append(id)
        try await Task.sleep(nanoseconds: 5_000_000)
        return try startResult.get()
    }
}
