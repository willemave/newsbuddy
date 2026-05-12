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
              "is_saved_to_knowledge": false,
              "summary": "Top level summary",
              "short_summary": "Top level summary",
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
              "is_saved_to_knowledge": false,
              "summary": "Top level summary",
              "short_summary": "Top level summary",
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
              "is_saved_to_knowledge": false,
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

    func testInterestingExternalLinksDecodeFromMetadata() throws {
        let detail = try decodeDetail(
            from: """
            {
              "id": 10,
              "content_type": "article",
              "url": "https://example.com/story-4",
              "title": "Story title",
              "display_title": "Display title",
              "source": "Example",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {
                "interesting_external_links": [
                  {
                    "url": "https://papers.example.org/model",
                    "title": "Original model paper",
                    "reason": "Primary source for the methodology.",
                    "category": "primary_source",
                    "confidence": 0.95
                  }
                ]
              },
              "created_at": "2026-04-02T10:00:00Z",
              "updated_at": null,
              "processed_at": "2026-04-02T10:05:00Z",
              "checked_out_by": null,
              "checked_out_at": null,
              "publication_date": null,
              "is_read": false,
              "is_saved_to_knowledge": false,
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

        XCTAssertEqual(detail.interestingExternalLinks.count, 1)
        XCTAssertEqual(detail.interestingExternalLinks[0].url, "https://papers.example.org/model")
        XCTAssertEqual(detail.interestingExternalLinks[0].title, "Original model paper")
        XCTAssertEqual(
            detail.interestingExternalLinks[0].reason,
            "Primary source for the methodology."
        )
    }

    func testLongformArtifactDetailSectionsUseReaderOrderAndPreferredExtras() throws {
        let artifact = try decodeArtifact(
            from: """
            {
              "title": "Artifact title",
              "one_line": "A concise description of why the argument matters now.",
              "ask": "judge",
              "artifact": {
                "type": "argument",
                "payload": {
                  "overview": "This overview stays in the raw payload but is not the lead detail section.",
                  "quotes": [
                    {
                      "text": "The first source quote gives the reader concrete evidence.",
                      "attribution": "Source A"
                    }
                  ],
                  "extras": {
                    "thesis": "The source argues that reliable workflows matter more than isolated demos.",
                    "evidence": ["The article cites adoption data from a named workflow."],
                    "mental_model": ["Judge the system by repeated workflow reliability."],
                    "counterpoint": "A fair objection is that demos can still expose important capabilities.",
                    "arguments": ["The argument is supported by operational examples."]
                  },
                  "key_points": [
                    {
                      "heading": "Workflow Reliability",
                      "content": "The piece says repeated reliability matters more than isolated performance."
                    }
                  ],
                  "takeaway": "Judge the claim by its evidence and tradeoffs."
                }
              }
            }
            """
        )

        XCTAssertEqual(
            artifact.detailSections.map(\.kind),
            [.takeaway, .keyPoints, .sourceQuotes, .extra]
        )

        guard case .extra(let extraSections) = artifact.detailSections.last else {
            return XCTFail("Expected final long-form artifact detail section to be Extra")
        }

        XCTAssertEqual(
            extraSections.map(\.title),
            ["Evidence", "Mental Model", "Counter Arguments", "Supporting Arguments", "Thesis"]
        )
        XCTAssertEqual(
            extraSections[0].items,
            ["The article cites adoption data from a named workflow."]
        )
        XCTAssertEqual(
            extraSections[1].items,
            ["Judge the system by repeated workflow reliability."]
        )
        XCTAssertEqual(
            extraSections[2].items,
            ["Counterpoint: A fair objection is that demos can still expose important capabilities."]
        )
        XCTAssertEqual(
            extraSections[3].items,
            ["Arguments: The argument is supported by operational examples."]
        )
    }

    private func decodeDetail(from json: String) throws -> ContentDetail {
        let data = Data(json.utf8)
        return try JSONDecoder().decode(ContentDetail.self, from: data)
    }

    private func decodeArtifact(from json: String) throws -> LongformArtifactEnvelope {
        let data = Data(json.utf8)
        return try JSONDecoder().decode(LongformArtifactEnvelope.self, from: data)
    }
}
