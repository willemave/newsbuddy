//
//  ContentDetailTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

final class ContentDetailTests: XCTestCase {
    func testResolvedNewsFieldsFallbackToTopLevelPayload() throws {
        let detail = try decodeDetail(
            from: """
            {
              "id": 7,
              "content_type": "news",
              "url": "https://example.com/story",
              "source_url": "https://example.com/story",
              "discussion_url": "https://news.ycombinator.com/item?id=7",
              "title": "Story title",
              "display_title": "Display title",
              "source": "Hacker News",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {},
              "created_at": "2026-04-02T10:00:00Z",
              "updated_at": null,
              "processed_at": "2026-04-02T10:05:00Z",
              "checked_out_by": null,
              "checked_out_at": null,
              "publication_date": "2026-04-02T09:00:00Z",
              "is_read": false,
              "is_favorited": false,
              "summary": "Top level summary",
              "short_summary": "Top level summary",
              "summary_kind": null,
              "summary_version": null,
              "structured_summary": null,
              "bullet_points": [],
              "quotes": [],
              "topics": [],
              "full_markdown": null,
              "news_article_url": "https://example.com/story",
              "news_discussion_url": "https://news.ycombinator.com/item?id=7",
              "news_key_points": ["Point one", "Point two"],
              "news_summary": "Top level summary",
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )

        XCTAssertEqual(detail.resolvedNewsSummaryText, "Top level summary")
        XCTAssertEqual(detail.resolvedNewsArticleURL, "https://example.com/story")
        XCTAssertEqual(detail.resolvedNewsKeyPoints, ["Point one", "Point two"])
    }

    func testResolvedNewsFieldsPreferMetadataSummaryWhenPresent() throws {
        let detail = try decodeDetail(
            from: """
            {
              "id": 8,
              "content_type": "news",
              "url": "https://example.com/story-2",
              "source_url": "https://example.com/story-2",
              "discussion_url": "https://news.ycombinator.com/item?id=8",
              "title": "Story title",
              "display_title": "Display title",
              "source": "Hacker News",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {
                "summary": {
                  "article_url": "https://example.com/story-2/metadata",
                  "summary": "Metadata summary",
                  "key_points": ["Metadata point"]
                }
              },
              "created_at": "2026-04-02T10:00:00Z",
              "updated_at": null,
              "processed_at": "2026-04-02T10:05:00Z",
              "checked_out_by": null,
              "checked_out_at": null,
              "publication_date": "2026-04-02T09:00:00Z",
              "is_read": false,
              "is_favorited": false,
              "summary": "Top level summary",
              "short_summary": "Top level summary",
              "summary_kind": null,
              "summary_version": null,
              "structured_summary": null,
              "bullet_points": [],
              "quotes": [],
              "topics": [],
              "full_markdown": null,
              "news_article_url": "https://example.com/story-2",
              "news_discussion_url": "https://news.ycombinator.com/item?id=8",
              "news_key_points": ["Top level point"],
              "news_summary": "Top level summary",
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )

        XCTAssertEqual(detail.resolvedNewsSummaryText, "Metadata summary")
        XCTAssertEqual(detail.resolvedNewsArticleURL, "https://example.com/story-2/metadata")
        XCTAssertEqual(detail.resolvedNewsKeyPoints, ["Metadata point"])
    }

    func testPrimaryTimestampPrefersPublicationDate() throws {
        let detail = try decodeDetail(
            from: """
            {
              "id": 9,
              "content_type": "news",
              "url": "https://example.com/story-3",
              "title": "Story title",
              "display_title": "Display title",
              "source": "Techmeme",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {},
              "created_at": "2026-04-02T10:00:00Z",
              "updated_at": null,
              "processed_at": "2026-04-02T10:05:00Z",
              "checked_out_by": null,
              "checked_out_at": null,
              "publication_date": "2026-04-02T09:00:00Z",
              "is_read": false,
              "is_favorited": false,
              "summary": null,
              "short_summary": null,
              "summary_kind": null,
              "summary_version": null,
              "structured_summary": null,
              "bullet_points": [],
              "quotes": [],
              "topics": [],
              "full_markdown": null,
              "body_available": false,
              "body_kind": null,
              "body_format": null,
              "news_article_url": null,
              "news_discussion_url": null,
              "news_key_points": [],
              "news_summary": null,
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )

        XCTAssertEqual(detail.primaryTimestamp, "2026-04-02T09:00:00Z")
    }

    private func decodeDetail(from json: String) throws -> ContentDetail {
        let data = Data(json.utf8)
        return try JSONDecoder().decode(ContentDetail.self, from: data)
    }
}
