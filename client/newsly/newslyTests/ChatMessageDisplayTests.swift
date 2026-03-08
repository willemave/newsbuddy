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
}
