//
//  ContentDetailTests.swift
//  newslyTests
//

import CoreGraphics
import XCTest
@testable import newsly

final class ContentDetailTests: XCTestCase {
    func testDetailSwipeIgnoresInteriorTextSelectionDrag() {
        XCTAssertNil(
            DetailSwipePolicy.dragOffset(
                origin: .content,
                translation: CGSize(width: -120, height: 6),
                currentIndex: 0,
                itemCount: 2
            )
        )

        XCTAssertNil(
            DetailSwipePolicy.dragOffset(
                origin: .content,
                translation: CGSize(width: 120, height: 6),
                currentIndex: 1,
                itemCount: 2
            )
        )

        XCTAssertEqual(
            DetailSwipePolicy.endAction(
                origin: .content,
                translation: CGSize(width: -120, height: 6),
                currentIndex: 0,
                itemCount: 2
            ),
            .ignore
        )

        XCTAssertEqual(
            DetailSwipePolicy.endAction(
                origin: .content,
                translation: CGSize(width: 120, height: 6),
                currentIndex: 1,
                itemCount: 2
            ),
            .ignore
        )
    }

    func testDetailSwipeUsesLeftEdgeForBackAndRightEdgeForForward() {
        XCTAssertEqual(
            DetailSwipePolicy.endAction(
                origin: .leadingEdge,
                translation: CGSize(width: 120, height: 6),
                currentIndex: 1,
                itemCount: 3,
                leadingEdgePreviousEnabled: true
            ),
            .previous
        )

        XCTAssertEqual(
            DetailSwipePolicy.endAction(
                origin: .trailingEdge,
                translation: CGSize(width: -120, height: 6),
                currentIndex: 1,
                itemCount: 3,
                leadingEdgePreviousEnabled: true
            ),
            .next
        )

        XCTAssertEqual(
            DetailSwipePolicy.endAction(
                origin: .trailingEdge,
                translation: CGSize(width: 120, height: 6),
                currentIndex: 1,
                itemCount: 3,
                leadingEdgePreviousEnabled: true
            ),
            .ignore
        )

        XCTAssertEqual(
            DetailSwipePolicy.endAction(
                origin: .leadingEdge,
                translation: CGSize(width: 120, height: 6),
                currentIndex: 0,
                itemCount: 1,
                leadingEdgePreviousEnabled: true
            ),
            .dismiss
        )
    }

    func testFastNewsLeftEdgeSwipeDismissesInsteadOfNavigatingPrevious() {
        XCTAssertEqual(
            DetailSwipePolicy.endAction(
                origin: .leadingEdge,
                translation: CGSize(width: 120, height: 6),
                currentIndex: 2,
                itemCount: 5,
                leadingEdgePreviousEnabled: false
            ),
            .dismiss
        )
    }

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

    func testNewsDetailDecodesSourceMetadata() throws {
        let detail = try decodeDetail(
            from: """
            {
              "id": 10,
              "content_type": "news",
              "url": "https://arxiv.org/abs/2509.15194v2",
              "source_url": "https://news.ycombinator.com/item?id=10",
              "discussion_url": "https://news.ycombinator.com/item?id=10",
              "title": "Paper title",
              "display_title": "Paper title",
              "source": "Hacker News",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {
                "source_metadata": {
                  "schema_version": 1,
                  "kind": "research_paper",
                  "provider": "arxiv",
                  "source_id": "2509.15194v2",
                  "canonical_abs_url": "https://arxiv.org/abs/2509.15194v2",
                  "brief_synopsis": "A compact synopsis.",
                  "authors": [
                    {
                      "name": "Ada Lovelace",
                      "affiliation": "Analytical Engines Lab",
                      "affiliation_source": "arxiv_api",
                      "confidence": "direct"
                    }
                  ],
                  "categories": [{"term": "cs.AI", "primary": true}],
                  "published_at": "2026-01-01T00:00:00Z"
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
              "news_article_url": "https://arxiv.org/abs/2509.15194v2",
              "news_discussion_url": "https://news.ycombinator.com/item?id=10",
              "news_key_points": ["Point one"],
              "news_summary": "Top level summary",
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )

        let metadata = try XCTUnwrap(detail.sourceMetadata)
        XCTAssertEqual(metadata.sourceID, "2509.15194v2")
        XCTAssertEqual(metadata.displaySynopsis, "A compact synopsis.")
        XCTAssertEqual(metadata.displayAuthors.first?.displayAffiliation, "Analytical Engines Lab")
        XCTAssertEqual(metadata.categoryLine, "cs.AI")
        XCTAssertEqual(metadata.arxivURL, "https://arxiv.org/abs/2509.15194v2")
    }

    func testArticleDetailDecodesSourceMetadata() throws {
        let detail = try decodeDetail(
            from: """
            {
              "id": 11,
              "content_type": "article",
              "url": "https://arxiv.org/abs/2509.15194v2",
              "source_url": "https://arxiv.org/abs/2509.15194v2",
              "discussion_url": null,
              "title": "Paper title",
              "display_title": "Paper title",
              "source": "arxiv.org",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {
                "source_metadata": {
                  "schema_version": 1,
                  "kind": "research_paper",
                  "provider": "arxiv",
                  "source_id": "2509.15194v2",
                  "canonical_abs_url": "https://arxiv.org/abs/2509.15194v2",
                  "brief_synopsis": "A compact article synopsis.",
                  "authors": [{"name": "Grace Hopper"}],
                  "categories": [{"term": "cs.CL", "primary": true}]
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
              "news_article_url": null,
              "news_discussion_url": null,
              "news_key_points": null,
              "news_summary": null,
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )

        let metadata = try XCTUnwrap(detail.sourceMetadata)
        XCTAssertEqual(metadata.sourceID, "2509.15194v2")
        XCTAssertEqual(metadata.displaySynopsis, "A compact article synopsis.")
        XCTAssertEqual(metadata.displayAuthors.first?.displayName, "Grace Hopper")
        XCTAssertEqual(metadata.categoryLine, "cs.CL")
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

        XCTAssertEqual(
            ServerDate.parse(detail.primaryTimestamp),
            ServerDate.parse("2026-04-02T09:00:00Z")
        )
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
        XCTAssertEqual(detail.relevantLinks.map(\.url), ["https://papers.example.org/model"])
    }

    func testNewsRelevantLinksDecodeAndExcludePrimaryURLs() throws {
        let detail = try decodeDetail(
            from: """
            {
              "id": 11,
              "content_type": "news",
              "url": "https://example.com/story-5",
              "source_url": "https://news.ycombinator.com/item?id=11",
              "discussion_url": "https://news.ycombinator.com/item?id=11",
              "title": "Story title",
              "display_title": "Display title",
              "source": "Hacker News",
              "status": "completed",
              "error_message": null,
              "retry_count": 0,
              "metadata": {
                "article": {
                  "url": "https://example.com/story-5",
                  "title": "Article title"
                },
                "discussion_url": "https://news.ycombinator.com/item?id=11",
                "relevant_links": [
                  {
                    "url": "https://example.com/story-5",
                    "title": "Primary article",
                    "reason": "Should be excluded.",
                    "source": "article"
                  },
                  {
                    "url": "https://docs.example.com/api",
                    "title": "API docs",
                    "reason": "Explains the API surface.",
                    "source": "article"
                  },
                  {
                    "url": "https://github.com/example/project",
                    "title": "Project repo",
                    "reason": "Commenters pointed to the implementation.",
                    "source": "community"
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
              "news_article_url": "https://example.com/story-5",
              "news_discussion_url": "https://news.ycombinator.com/item?id=11",
              "news_key_points": [],
              "news_summary": null,
              "image_url": null,
              "thumbnail_url": null,
              "detected_feed": null,
              "can_subscribe": false
            }
            """
        )

        XCTAssertEqual(
            detail.relevantLinks.map(\.url),
            [
                "https://docs.example.com/api",
                "https://github.com/example/project"
            ]
        )
        XCTAssertEqual(detail.relevantLinks[0].source, "article")
        XCTAssertEqual(detail.relevantLinks[1].source, "community")
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

        XCTAssertNil(artifact.artifact.payload.overview)
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

    func testNewsDetailMapsFromCanonicalNewsWireContract() throws {
        let json = """
        {
          "id": 91,
          "content_type": "news",
          "url": "https://example.com/news/91",
          "source_url": null,
          "discussion_url": "https://example.com/discuss/91",
          "title": "Canonical News title",
          "display_title": "Canonical News title",
          "source": "Example News",
          "status": "completed",
          "retry_count": 0,
          "metadata": {},
          "created_at": "2026-08-31T12:00:00Z",
          "updated_at": null,
          "processed_at": "2026-08-31T12:01:00Z",
          "publication_date": "2026-08-31T11:55:00Z",
          "is_read": true,
          "is_saved_to_knowledge": false,
          "summary": "Canonical summary",
          "short_summary": "Short summary",
          "body_available": false,
          "body_kind": null,
          "body_format": null,
          "news_article_url": "https://example.com/news/91",
          "news_discussion_url": "https://example.com/discuss/91",
          "news_key_points": ["One key point"],
          "news_summary": "News summary",
          "can_subscribe": false
        }
        """

        let wire = try JSONDecoder().decode(
            APINewsItemDetailResponse.self,
            from: Data(json.utf8)
        )
        let detail = ContentDetail(api: wire)

        XCTAssertEqual(detail.id, 91)
        XCTAssertEqual(detail.contentType, .news)
        XCTAssertEqual(detail.displayTitle, "Canonical News title")
        XCTAssertEqual(detail.newsDiscussionURL, "https://example.com/discuss/91")
        XCTAssertEqual(detail.newsKeyPoints, ["One key point"])
        XCTAssertEqual(
            ServerDate.parse(detail.primaryTimestamp),
            ServerDate.parse("2026-08-31T11:55:00Z")
        )
        XCTAssertTrue(detail.isRead)
        XCTAssertFalse(detail.isSavedToKnowledge)

        XCTAssertNil(detail.errorMessage)
        XCTAssertNil(detail.checkedOutBy)
        XCTAssertNil(detail.checkedOutAt)
        XCTAssertNil(detail.summaryKind)
        XCTAssertNil(detail.structuredSummaryRaw)
        XCTAssertNil(detail.longformArtifactRaw)
        XCTAssertNil(detail.feedPreview)
        XCTAssertNil(detail.imageUrl)
        XCTAssertNil(detail.detectedFeed)
        XCTAssertTrue(detail.bulletPoints.isEmpty)
        XCTAssertTrue(detail.quotes.isEmpty)
        XCTAssertTrue(detail.topics.isEmpty)
    }

    func testNewsListMapsCanonicalNewsSummariesWithoutContentOnlyFields() throws {
        let json = """
        {
          "contents": [
            {
              "id": 92,
              "content_type": "news",
              "url": "https://example.com/news/92",
              "source_url": null,
              "discussion_url": null,
              "title": "News list title",
              "source": "Example News",
              "platform": "example",
              "status": "completed",
              "short_summary": "List summary",
              "created_at": "2026-08-31T12:00:00Z",
              "processed_at": null,
              "classification": "to_read",
              "publication_date": null,
              "is_read": false,
              "is_saved_to_knowledge": true,
              "news_article_url": "https://example.com/news/92",
              "news_discussion_url": null,
              "news_key_points": ["List key point"],
              "news_summary": "List news summary",
              "top_comment": {"author": "reader", "text": "Useful context"},
              "comment_count": 7
            }
          ],
          "available_dates": ["2026-08-31"],
          "content_types": ["news"],
          "meta": {
            "next_cursor": null,
            "has_more": false,
            "page_size": 1,
            "total": null
          }
        }
        """

        let wire = try JSONDecoder().decode(
            APINewsItemListResponse.self,
            from: Data(json.utf8)
        )
        let response = ContentListResponse(api: wire)
        let summary = try XCTUnwrap(response.contents.first)

        XCTAssertEqual(response.contentTypes, [APIContentType.news.rawValue])
        XCTAssertEqual(response.availableDates, ["2026-08-31"])
        XCTAssertNil(response.total)
        XCTAssertFalse(response.hasMore)
        XCTAssertEqual(summary.id, 92)
        XCTAssertEqual(summary.contentType, .news)
        XCTAssertEqual(summary.classification, APIContentClassification.to_read.rawValue)
        XCTAssertEqual(summary.newsKeyPoints, ["List key point"])
        XCTAssertEqual(
            summary.topComment,
            ContentSummary.TopComment(author: "reader", text: "Useful context")
        )
        XCTAssertEqual(summary.commentCount, 7)
        XCTAssertTrue(summary.isSavedToKnowledge)

        XCTAssertNil(summary.knowledgeSavedAt)
        XCTAssertNil(summary.imageUrl)
        XCTAssertNil(summary.thumbnailUrl)
        XCTAssertNil(summary.feedPreview)
        XCTAssertNil(summary.savedSource)
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
