//
//  ChatMessageDisplayTests.swift
//  newslyTests
//

import Foundation
import XCTest
@testable import newsly

final class ChatMessageDisplayTests: XCTestCase {
    func testChatMessageDecodesProcessSummaryDisplayMetadata() throws {
        let data = Data(
            """
            {
              "id": 7,
              "session_id": 21,
              "role": "tool",
              "content": "Thinking • Searched the web and reviewed sources",
              "timestamp": "2026-03-08T18:00:00Z",
              "display_type": "process_summary",
              "process_label": "Thinking • Searched the web and reviewed sources",
              "status": "completed",
              "error": null
            }
            """.utf8
        )

        let message = try JSONDecoder().decode(ChatMessage.self, from: data)

        XCTAssertEqual(message.role, .tool)
        XCTAssertEqual(message.displayType, .processSummary)
        XCTAssertTrue(message.isProcessSummary)
        XCTAssertEqual(message.processSummaryText, "Thinking • Searched the web and reviewed sources")
    }

    func testChatSessionDetailPreservesProcessSummaryOrdering() throws {
        let data = Data(
            """
            {
              "session": {
                "id": 42,
                "content_id": null,
                "title": "Daily AI Digest",
                "session_type": "news_digest_brain",
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
                "has_pending_message": false,
                "is_favorite": false,
                "has_messages": true,
                "last_message_preview": "Final deep-dive answer.",
                "last_message_role": "assistant"
              },
              "messages": [
                {
                  "id": 1,
                  "session_id": 42,
                  "role": "user",
                  "content": "Dig deeper into these digest bullets.",
                  "timestamp": "2026-03-08T18:00:00Z",
                  "status": "completed",
                  "error": null
                },
                {
                  "id": 2,
                  "session_id": 42,
                  "role": "tool",
                  "content": "Thinking • Searched the web and reviewed sources",
                  "timestamp": "2026-03-08T18:00:01Z",
                  "display_type": "process_summary",
                  "process_label": "Thinking • Searched the web and reviewed sources",
                  "status": "completed",
                  "error": null
                },
                {
                  "id": 3,
                  "session_id": 42,
                  "role": "assistant",
                  "content": "Final deep-dive answer.",
                  "timestamp": "2026-03-08T18:00:02Z",
                  "status": "completed",
                  "error": null
                }
              ]
            }
            """.utf8
        )

        let detail = try JSONDecoder().decode(ChatSessionDetail.self, from: data)

        XCTAssertEqual(detail.messages.map(\.role), [.user, .tool, .assistant])
        XCTAssertTrue(detail.messages[1].isProcessSummary)
        XCTAssertEqual(detail.messages[2].content, "Final deep-dive answer.")
    }

    func testChatMessageDecodesAssistantFeedOptions() throws {
        let data = Data(
            """
            {
              "id": 8,
              "session_id": 21,
              "role": "assistant",
              "content": "I found a few good matches below.",
              "timestamp": "2026-03-17T18:00:00Z",
              "status": "completed",
              "error": null,
              "feed_options": [
                {
                  "id": "8f7d2c42b0c1de90",
                  "title": "lucumr",
                  "site_url": "https://lucumr.pocoo.org/",
                  "feed_url": "https://lucumr.pocoo.org/feed.atom",
                  "feed_type": "atom",
                  "feed_format": "atom",
                  "description": "Armin Ronacher's weblog.",
                  "rationale": "Validated Atom feed for Armin Ronacher's blog.",
                  "evidence_url": "https://lucumr.pocoo.org/"
                }
              ]
            }
            """.utf8
        )

        let message = try JSONDecoder().decode(ChatMessage.self, from: data)

        XCTAssertTrue(message.hasFeedOptions)
        XCTAssertEqual(message.feedOptions.count, 1)
        XCTAssertEqual(message.feedOptions[0].title, "lucumr")
        XCTAssertEqual(message.feedOptions[0].feedTypeLabel, "Atom")
    }

    func testChatMessageDecodesCouncilCandidates() throws {
        let data = Data(
            """
            {
              "id": 12,
              "session_id": 21,
              "role": "assistant",
              "content": "Analyst branch",
              "timestamp": "2026-03-30T18:00:00Z",
              "status": "completed",
              "error": null,
              "active_council_child_session_id": 201,
              "council_candidates": [
                {
                  "persona_id": "analyst",
                  "persona_name": "Analyst",
                  "child_session_id": 201,
                  "content": "Analyst branch",
                  "status": "completed",
                  "order": 0
                },
                {
                  "persona_id": "skeptic",
                  "persona_name": "Skeptic",
                  "child_session_id": 202,
                  "content": "Skeptic branch",
                  "status": "completed",
                  "order": 1
                }
              ]
            }
            """.utf8
        )

        let message = try JSONDecoder().decode(ChatMessage.self, from: data)

        XCTAssertTrue(message.hasCouncilCandidates)
        XCTAssertEqual(message.activeCouncilChildSessionId, 201)
        XCTAssertEqual(message.councilCandidates.map(\.personaName), ["Analyst", "Skeptic"])
    }

    func testChatSessionDetailDecodesCouncilModeSummaryAndMessages() throws {
        let data = Data(
            """
            {
              "session": {
                "id": 42,
                "content_id": null,
                "title": "Council Chat",
                "session_type": "knowledge_chat",
                "topic": null,
                "llm_provider": "openai",
                "llm_model": "openai:gpt-5.4",
                "created_at": "2026-03-30T18:00:00Z",
                "updated_at": "2026-03-30T18:02:00Z",
                "last_message_at": "2026-03-30T18:02:00Z",
                "is_archived": false,
                "article_title": null,
                "article_url": null,
                "article_summary": null,
                "article_source": null,
                "has_pending_message": false,
                "is_favorite": false,
                "has_messages": true,
                "last_message_preview": "Analyst branch",
                "last_message_role": "assistant",
                "council_mode": true,
                "active_child_session_id": 201
              },
              "messages": [
                {
                  "id": 1,
                  "session_id": 42,
                  "role": "assistant",
                  "content": "Analyst branch",
                  "timestamp": "2026-03-30T18:01:00Z",
                  "status": "completed",
                  "error": null,
                  "active_council_child_session_id": 201,
                  "council_candidates": [
                    {
                      "persona_id": "analyst",
                      "persona_name": "Analyst",
                      "child_session_id": 201,
                      "content": "Analyst branch",
                      "status": "completed",
                      "order": 0
                    },
                    {
                      "persona_id": "skeptic",
                      "persona_name": "Skeptic",
                      "child_session_id": 202,
                      "content": "Skeptic branch",
                      "status": "completed",
                      "order": 1
                    }
                  ]
                }
              ]
            }
            """.utf8
        )

        let detail = try JSONDecoder().decode(ChatSessionDetail.self, from: data)

        XCTAssertTrue(detail.session.isCouncilMode)
        XCTAssertEqual(detail.session.activeChildSessionId, 201)
        XCTAssertEqual(detail.messages.first?.councilCandidates.count, 2)
    }

    @MainActor
    func testAssistantFeedOptionActionModelMarksHttp400AsSubscribed() async {
        let option = AssistantFeedOption(
            id: "8f7d2c42b0c1de90",
            title: "lucumr",
            siteURL: "https://lucumr.pocoo.org/",
            feedURL: "https://lucumr.pocoo.org/feed.atom",
            feedType: "atom",
            feedFormat: "atom",
            description: nil,
            rationale: nil,
            evidenceURL: nil
        )
        let model = AssistantFeedOptionActionModel(
            service: MockAssistantFeedSubscriptionService(
                result: .failure(APIError.httpError(statusCode: 400))
            )
        )

        await model.subscribe(option)

        XCTAssertTrue(model.isSubscribed(option))
        XCTAssertFalse(model.isSubscribing(option))
    }
}

@MainActor
private final class MockAssistantFeedSubscriptionService: AssistantFeedSubscribing {
    let result: Result<ScraperConfig, Error>

    init(result: Result<ScraperConfig, Error>) {
        self.result = result
    }

    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig {
        _ = (feedURL, feedType, displayName)
        return try result.get()
    }
}
