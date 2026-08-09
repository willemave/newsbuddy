//
//  ChatMessageDisplayTests.swift
//  newslyTests
//

import Foundation
import XCTest
@testable import newsly

final class ChatMessageDisplayTests: XCTestCase {
    func testChatShareTextUsesPlainMarkdownContent() {
        let content = ShareContent(
            messageContent: "Here is **bold** text with [a source](https://example.com).",
            articleTitle: "Chat context",
            articleUrl: "https://news.example/story"
        )

        XCTAssertEqual(
            content.shareText,
            "Chat context\n\nHere is bold text with a source.\n\nhttps://news.example/story"
        )
    }

    func testChatMessageDecodesProcessSummaryDisplayMetadata() throws {
        let data = Data(
            """
            {
              "id": 7,
              "session_id": 21,
              "display_key": "21|7|tool|process_summary",
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
        XCTAssertEqual(message.displayType, .process_summary)
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
                "title": "Daily AI Brief",
                "session_type": "article_brain",
                "topic": null,
                "llm_provider": "anthropic",
                "llm_model": "anthropic:claude-opus-4-6",
                "created_at": "2026-03-08T18:00:00Z",
                "updated_at": null,
                "last_message_at": null,
                "is_archived": false,
                "article_title": null,
                "article_url": null,
                "article_summary": null,
                "article_source": null,
                "has_pending_message": false,
                "is_waiting_for_content": false,
                "is_saved_to_knowledge": false,
                "has_messages": true,
                "last_message_preview": "Final deep-dive answer.",
                "last_message_role": "assistant",
                "council_mode": false
              },
              "messages": [
                {
                  "id": 1,
                  "session_id": 42,
                  "display_key": "42|1|user|message",
                  "role": "user",
                  "content": "Dig deeper into these news bullets.",
                  "timestamp": "2026-03-08T18:00:00Z",
                  "display_type": "message",
                  "status": "completed",
                  "error": null
                },
                {
                  "id": 2,
                  "session_id": 42,
                  "display_key": "42|2|tool|process_summary",
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
                  "display_key": "42|3|assistant|message",
                  "role": "assistant",
                  "content": "Final deep-dive answer.",
                  "timestamp": "2026-03-08T18:00:02Z",
                  "display_type": "message",
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
              "display_key": "21|8|assistant|message",
              "role": "assistant",
              "content": "I found a few good matches below.",
              "timestamp": "2026-03-17T18:00:00Z",
              "display_type": "message",
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
        XCTAssertFalse(message.feedOptions[0].isSubscribed)
    }

    func testChatMessageDecodesCouncilCandidates() throws {
        let data = Data(
            """
            {
              "id": 12,
              "session_id": 21,
              "display_key": "21|12|assistant|message",
              "role": "assistant",
              "content": "Analyst branch",
              "timestamp": "2026-03-30T18:00:00Z",
              "display_type": "message",
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
                "llm_model": "openai:gpt-5.5",
                "created_at": "2026-03-30T18:00:00Z",
                "updated_at": "2026-03-30T18:02:00Z",
                "last_message_at": "2026-03-30T18:02:00Z",
                "is_archived": false,
                "article_title": null,
                "article_url": null,
                "article_summary": null,
                "article_source": null,
                "has_pending_message": false,
                "is_waiting_for_content": false,
                "is_saved_to_knowledge": false,
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
                  "display_key": "42|1|assistant|message",
                  "role": "assistant",
                  "content": "Analyst branch",
                  "timestamp": "2026-03-30T18:01:00Z",
                  "display_type": "message",
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
    func testAssistantFeedOptionActionModelUsesTypedAlreadySubscribedOutcome() async {
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
                result: .success(
                    ScraperConfig(
                        id: 7,
                        scraperType: "feed",
                        config: [:],
                        feedUrl: option.feedURL,
                        isActive: true,
                        createdAt: Date(),
                        subscriptionOutcome: .already_subscribed
                    )
                )
            )
        )

        await model.subscribe(option)

        XCTAssertTrue(model.isSubscribed(option))
        XCTAssertFalse(model.isSubscribing(option))
        XCTAssertEqual(model.subscriptionLabels[option.id], "Already subscribed")
    }

    @MainActor
    func testAssistantFeedOptionActionModelUsesReactivatedOutcome() async {
        let option = AssistantFeedOption(
            id: "paused-feed",
            title: "Paused Feed",
            siteURL: "https://example.com/",
            feedURL: "https://example.com/paused.xml",
            feedType: "atom",
            feedFormat: "rss"
        )
        let service = MockAssistantFeedSubscriptionService(
            result: .success(
                ScraperConfig(
                    id: 8,
                    scraperType: "atom",
                    config: [:],
                    feedUrl: option.feedURL,
                    isActive: true,
                    createdAt: Date(),
                    subscriptionOutcome: .reactivated
                )
            )
        )
        let model = AssistantFeedOptionActionModel(service: service)

        await model.subscribe(option)

        XCTAssertTrue(model.isSubscribed(option))
        XCTAssertFalse(model.isSubscribing(option))
        XCTAssertEqual(model.subscriptionLabels[option.id], "Re-enabled")
        XCTAssertEqual(service.callCount, 1)
    }

    @MainActor
    func testAssistantFeedOptionActionModelDoesNotTreatHttp400AsSuccess() async {
        let option = AssistantFeedOption(
            id: "bad-feed",
            title: "Bad feed",
            siteURL: "https://example.com/",
            feedURL: "https://example.com/feed.xml",
            feedType: "rss",
            feedFormat: "rss",
            description: nil,
            rationale: nil,
            evidenceURL: nil
        )
        let model = AssistantFeedOptionActionModel(
            service: MockAssistantFeedSubscriptionService(
                result: .failure(APIError.httpError(statusCode: 400, detail: nil))
            )
        )

        await model.subscribe(option)

        XCTAssertFalse(model.isSubscribed(option))
        XCTAssertFalse(model.isSubscribing(option))
    }

    @MainActor
    func testAssistantFeedOptionActionModelDoesNotResubscribeServerMarkedOption() async {
        let option = AssistantFeedOption(
            id: "active-feed",
            title: "Active feed",
            siteURL: "https://example.com/",
            feedURL: "https://example.com/feed.xml",
            feedType: "atom",
            feedFormat: "rss",
            isSubscribed: true
        )
        let service = MockAssistantFeedSubscriptionService(
            result: .failure(APIError.httpError(statusCode: 500, detail: nil))
        )
        let model = AssistantFeedOptionActionModel(service: service)

        await model.subscribe(option)

        XCTAssertTrue(model.isSubscribed(option))
        XCTAssertFalse(model.isSubscribing(option))
        XCTAssertEqual(service.callCount, 0)
    }
}

@MainActor
private final class MockAssistantFeedSubscriptionService: AssistantFeedSubscribing {
    let result: Result<ScraperConfig, Error>
    private(set) var callCount = 0

    init(result: Result<ScraperConfig, Error>) {
        self.result = result
    }

    func subscribeFeed(
        feedURL: String,
        feedType: String,
        displayName: String?
    ) async throws -> ScraperConfig {
        _ = (feedURL, feedType, displayName)
        callCount += 1
        return try result.get()
    }
}
