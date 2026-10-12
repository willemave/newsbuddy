//
//  ChatMessageDisplayTests.swift
//  newslyTests
//

import Foundation
import XCTest
@testable import newsly

final class ChatMessageDisplayTests: XCTestCase {
    func testFailedMessagePreservesStableDisplayIdentity() {
        let message = ChatMessage(
            id: 7,
            sourceMessageId: 3,
            displayKey: "stable-display-key",
            role: .user,
            timestamp: Date(timeIntervalSince1970: 100),
            content: "Try this",
            status: .processing
        )

        let failed = message.failed(error: "Unavailable")

        XCTAssertEqual(failed.displayKey, message.displayKey)
        XCTAssertEqual(failed.sourceMessageId, message.sourceMessageId)
        XCTAssertEqual(failed.status, .failed)
        XCTAssertEqual(failed.error, "Unavailable")
    }

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
              "source_message_id": null,
              "display_key": "21|7|tool|process_summary",
              "role": "tool",
              "content": "Thinking • Searched the web and reviewed sources",
              "timestamp": "2026-03-08T18:00:00Z",
              "display_type": "process_summary",
              "process_label": "Thinking • Searched the web and reviewed sources",
              "status": "completed",
              "error": null,
              "active_council_child_session_id": null
            }
            """.utf8
        )

        let message = ChatMessage(api: try JSONDecoder().decode(APIChatMessage.self, from: data))

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
                "news_item_id": null,
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
                "article_image_url": null,
                "article_thumbnail_url": null,
                "has_pending_message": false,
                "is_waiting_for_content": false,
                "is_saved_to_knowledge": false,
                "has_messages": true,
                "last_message_preview": "Final deep-dive answer.",
                "last_message_role": "assistant",
                "council_mode": false,
                "active_child_session_id": null
              },
              "messages": [
                {
                  "id": 1,
                  "session_id": 42,
                  "source_message_id": null,
                  "display_key": "42|1|user|message",
                  "role": "user",
                  "content": "Dig deeper into these news bullets.",
                  "timestamp": "2026-03-08T18:00:00Z",
                  "display_type": "message",
                  "process_label": null,
                  "status": "completed",
                  "error": null,
                  "active_council_child_session_id": null
                },
                {
                  "id": 2,
                  "session_id": 42,
                  "source_message_id": null,
                  "display_key": "42|2|tool|process_summary",
                  "role": "tool",
                  "content": "Thinking • Searched the web and reviewed sources",
                  "timestamp": "2026-03-08T18:00:01Z",
                  "display_type": "process_summary",
                  "process_label": "Thinking • Searched the web and reviewed sources",
                  "status": "completed",
                  "error": null,
                  "active_council_child_session_id": null
                },
                {
                  "id": 3,
                  "session_id": 42,
                  "source_message_id": null,
                  "display_key": "42|3|assistant|message",
                  "role": "assistant",
                  "content": "Final deep-dive answer.",
                  "timestamp": "2026-03-08T18:00:02Z",
                  "display_type": "message",
                  "process_label": null,
                  "status": "completed",
                  "error": null,
                  "active_council_child_session_id": null
                }
              ]
            }
            """.utf8
        )

        let detail = ChatSessionDetail(
            api: try JSONDecoder().decode(APIChatSessionDetail.self, from: data)
        )

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
              "source_message_id": null,
              "display_key": "21|8|assistant|message",
              "role": "assistant",
              "content": "I found a few good matches below.",
              "timestamp": "2026-03-17T18:00:00Z",
              "display_type": "message",
              "process_label": null,
              "status": "completed",
              "error": null,
              "active_council_child_session_id": null,
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

        let message = ChatMessage(api: try JSONDecoder().decode(APIChatMessage.self, from: data))

        XCTAssertTrue(message.hasFeedOptions)
        XCTAssertEqual(message.feedOptions.count, 1)
        XCTAssertEqual(message.feedOptions[0].title, "lucumr")
        XCTAssertEqual(message.feedOptions[0].feedTypeLabel, "Atom")
        XCTAssertFalse(message.feedOptions[0].isSubscribed)
        XCTAssertNil(message.feedOptions[0].publishingActivity)
    }

    func testAssistantSourceActivityKeepsUnknownAndZeroCountsDistinct() {
        let unknown = AssistantSourceActivityPresentation(activity: nil)
        XCTAssertEqual(unknown.quarterCountText, "90 days: count unavailable")
        XCTAssertEqual(unknown.yearCountText, "365 days: count unavailable")
        XCTAssertEqual(unknown.latestDateLabel, "Latest publication date unavailable")
        XCTAssertEqual(
            AssistantSourceActivityPresentation(activity: makeSourceActivity(basis: .newsly_corpus)).latestDateLabel,
            "Latest collection date unavailable"
        )

        let snapshot = AssistantSourceActivityPresentation(activity: makeSourceActivity(quarter: 0, year: nil))
        XCTAssertEqual(snapshot.quarterCountText, "90 days: 0 observed")
        XCTAssertEqual(snapshot.yearCountText, "365 days: count unavailable")
        XCTAssertEqual(snapshot.evidenceText, "Published items in the current feed; history may be incomplete.")
        XCTAssertEqual(snapshot.undatedText, "2 undated items excluded from counts.")
    }

    func testAssistantFeedOptionDecodesMeasuredActivityDatesAndCounts() throws {
        let data = Data("""
        {
          "id":"source123", "title":"Source", "site_url":"https://example.com",
          "feed_url":"https://example.com/feed", "feed_type":"atom", "feed_format":"rss",
          "description":null, "rationale":null, "evidence_url":null,
          "publishing_activity": {
            "basis":"feed_snapshot", "checked_at":"2026-10-05T12:00:00Z",
            "window_start":"2026-07-07T12:00:00Z", "year_window_start":"2025-10-05T12:00:00Z",
            "window_end":"2026-10-05T12:00:00Z", "observed_count":0,
            "observed_year_count":12, "undated_count":2,
            "latest_item": {"title":"Latest article", "url":"https://example.com/latest",
                            "published_at":"2026-06-27T12:00:00Z", "collected_at":null}
          }
        }
        """.utf8)
        let option = try JSONDecoder().decode(AssistantFeedOption.self, from: data)
        let activity = try XCTUnwrap(option.publishingActivity)
        XCTAssertEqual(activity.basis, .feed_snapshot)
        XCTAssertEqual(activity.checkedAt, ServerDate.parse("2026-10-05T12:00:00Z"))
        XCTAssertEqual(activity.observedCount, 0)
        XCTAssertEqual(activity.observedYearCount, 12)
        XCTAssertEqual(activity.latestItem?.publishedAt, ServerDate.parse("2026-06-27T12:00:00Z"))
        XCTAssertNil(activity.latestItem?.collectedAt)
    }

    func testAssistantSourceActivityDistinguishesCollectionDatesAndRejectsUnsafeLinks() {
        let collectedAt = Date(timeIntervalSince1970: 100)
        let activity = makeSourceActivity(
            basis: .newsly_corpus,
            latest: APIAssistantPublishedItem(title: "Paper", url: "javascript:alert(1)", publishedAt: nil, collectedAt: collectedAt)
        )
        let presentation = AssistantSourceActivityPresentation(activity: activity)
        XCTAssertEqual(presentation.latestDate, collectedAt)
        XCTAssertEqual(presentation.latestDateLabel, "Latest collected by Newsly")
        XCTAssertEqual(presentation.evidenceText, "Items collected by Newsly; publisher totals may differ.")
        XCTAssertNil(presentation.latestURL)

        let published = AssistantSourceActivityPresentation(activity: makeSourceActivity(
            latest: APIAssistantPublishedItem(title: nil, url: "https://example.com/latest", publishedAt: collectedAt, collectedAt: nil)
        ))
        XCTAssertEqual(published.latestDateLabel, "Latest observed publication")
        XCTAssertEqual(published.latestDisplayTitle, "Latest observed item")
        XCTAssertEqual(published.latestURL?.absoluteString, "https://example.com/latest")
    }

    func testAssistantFeedOptionEqualityIncludesActivity() {
        func option(_ activity: APIAssistantSourceActivity) -> AssistantFeedOption {
            AssistantFeedOption(
                id: "source", title: "Source", siteUrl: "https://example.com", feedUrl: "https://example.com/feed",
                feedType: .atom, description: nil, rationale: nil, evidenceUrl: nil, publishingActivity: activity
            )
        }
        XCTAssertEqual(option(makeSourceActivity(quarter: 3)), option(makeSourceActivity(quarter: 3)))
        XCTAssertNotEqual(option(makeSourceActivity(quarter: 3)), option(makeSourceActivity(quarter: 4)))
    }

    private func makeSourceActivity(
        basis: APISourceActivityBasis = .feed_snapshot,
        latest: APIAssistantPublishedItem? = nil,
        quarter: Int? = 3,
        year: Int? = 12
    ) -> APIAssistantSourceActivity {
        APIAssistantSourceActivity(
            basis: basis, checkedAt: Date(timeIntervalSince1970: 1_000),
            windowStart: Date(timeIntervalSince1970: 100), yearWindowStart: Date(timeIntervalSince1970: 0),
            windowEnd: Date(timeIntervalSince1970: 1_000), latestItem: latest,
            observedCount: quarter, observedYearCount: year, undatedCount: 2
        )
    }

    func testAssistantFeedOptionPresentsBuiltInAggregators() throws {
        let data = Data(
            """
            {
              "id": "5b2e0f4f9d1c7a33",
              "title": "arXiv",
              "site_url": "https://arxiv.org",
              "feed_url": "aggregator://arxiv",
              "feed_type": "aggregator",
              "feed_format": "rss",
              "description": "New AI and machine learning research papers, by arXiv category",
              "rationale": null,
              "evidence_url": "https://arxiv.org",
              "is_subscribed": true
            }
            """.utf8
        )

        let option = try JSONDecoder().decode(APIAssistantFeedOption.self, from: data)

        XCTAssertEqual(option.feedTypeLabel, "Aggregator")
        XCTAssertEqual(option.systemIcon, "doc.text.magnifyingglass")
        XCTAssertEqual(option.hostLabel, "arxiv.org")
        XCTAssertEqual(option.subtitleText, option.description)
        XCTAssertTrue(option.isSubscribed)
    }

    func testChatMessageIgnoresRetiredBranchMetadata() throws {
        let data = Data(
            """
            {
              "id": 12,
              "session_id": 21,
              "source_message_id": null,
              "display_key": "21|12|assistant|message",
              "role": "assistant",
              "content": "Analyst branch",
              "timestamp": "2026-03-30T18:00:00Z",
              "display_type": "message",
              "process_label": null,
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

        let message = ChatMessage(api: try JSONDecoder().decode(APIChatMessage.self, from: data))

        XCTAssertEqual(message.content, "Analyst branch")
        XCTAssertTrue(message.isAssistant)
    }

    func testChatSessionDetailIgnoresRetiredBranchMetadata() throws {
        let data = Data(
            """
            {
              "session": {
                "id": 42,
                "content_id": null,
                "news_item_id": null,
                "title": "Legacy Chat",
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
                "article_image_url": null,
                "article_thumbnail_url": null,
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
                  "source_message_id": null,
                  "display_key": "42|1|assistant|message",
                  "role": "assistant",
                  "content": "Analyst branch",
                  "timestamp": "2026-03-30T18:01:00Z",
                  "display_type": "message",
                  "process_label": null,
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

        let detail = ChatSessionDetail(
            api: try JSONDecoder().decode(APIChatSessionDetail.self, from: data)
        )

        XCTAssertEqual(detail.session.displayTitle, "Legacy Chat")
        XCTAssertEqual(detail.messages.first?.content, "Analyst branch")
    }

    @MainActor
    func testAssistantFeedOptionActionModelUsesTypedAlreadySubscribedOutcome() async {
        let option = AssistantFeedOption(
            id: "8f7d2c42b0c1de90",
            title: "lucumr",
            siteUrl: "https://lucumr.pocoo.org/",
            feedUrl: "https://lucumr.pocoo.org/feed.atom",
            feedType: .atom,
            feedFormat: .atom,
            description: nil,
            rationale: nil,
            evidenceUrl: nil
        )
        let model = AssistantFeedOptionActionModel(
            service: MockAssistantFeedSubscriptionService(
                result: .success(
                    ScraperConfig(
                        id: 7,
                        scraperType: "feed",
                        displayName: nil,
                        config: [:],
                        feedUrl: option.feedUrl,
                        limit: nil,
                        isActive: true,
                        createdAt: Date(),
                        stats: nil,
                        subscriptionOutcome: .already_subscribed,
                        backfillTaskId: nil
                    )
                )
            ),
            toastPresenter: AssistantFeedTestToastPresenter()
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
            siteUrl: "https://example.com/",
            feedUrl: "https://example.com/paused.xml",
            feedType: .atom,
            feedFormat: .rss,
            description: nil,
            rationale: nil,
            evidenceUrl: nil
        )
        let service = MockAssistantFeedSubscriptionService(
            result: .success(
                ScraperConfig(
                    id: 8,
                    scraperType: "atom",
                    displayName: nil,
                    config: [:],
                    feedUrl: option.feedUrl,
                    limit: nil,
                    isActive: true,
                    createdAt: Date(),
                    stats: nil,
                    subscriptionOutcome: .reactivated,
                    backfillTaskId: nil
                )
            )
        )
        let model = AssistantFeedOptionActionModel(
            service: service,
            toastPresenter: AssistantFeedTestToastPresenter()
        )

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
            siteUrl: "https://example.com/",
            feedUrl: "https://example.com/feed.xml",
            feedType: .unknown("rss"),
            feedFormat: .rss,
            description: nil,
            rationale: nil,
            evidenceUrl: nil
        )
        let model = AssistantFeedOptionActionModel(
            service: MockAssistantFeedSubscriptionService(
                result: .failure(ClientFailure.http(statusCode: 400, detail: nil))
            ),
            toastPresenter: AssistantFeedTestToastPresenter()
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
            siteUrl: "https://example.com/",
            feedUrl: "https://example.com/feed.xml",
            feedType: .atom,
            feedFormat: .rss,
            description: nil,
            rationale: nil,
            evidenceUrl: nil,
            isSubscribed: true
        )
        let service = MockAssistantFeedSubscriptionService(
            result: .failure(ClientFailure.http(statusCode: 500, detail: nil))
        )
        let model = AssistantFeedOptionActionModel(
            service: service,
            toastPresenter: AssistantFeedTestToastPresenter()
        )

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

@MainActor
private final class AssistantFeedTestToastPresenter: ToastPresenting {
    func show(_ message: String, type: ToastType, duration: TimeInterval) {}
    func showError(_ message: String) {}
    func showSuccess(_ message: String) {}
}
