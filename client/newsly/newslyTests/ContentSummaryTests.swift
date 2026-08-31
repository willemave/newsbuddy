//
//  ContentSummaryTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

final class ContentSummaryTests: XCTestCase {
    func testCalendarDayKeyUsesPublicationDateWhenAvailable() {
        let summary = makeSummary(
            createdAt: "2026-03-18T05:00:00Z",
            processedAt: "2026-03-18T06:00:00Z",
            publicationDate: "2026-03-17T23:30:00Z"
        )

        XCTAssertEqual(summary.calendarDayKey, "2026-03-17")
    }

    func testFormattedDateFallsBackToCreatedAtWhenProcessedAtMissing() {
        let summary = makeSummary(
            createdAt: "2026-03-18T05:00:00Z",
            processedAt: nil,
            publicationDate: nil
        )

        XCTAssertNotEqual(summary.formattedDate, "Date unknown")
        XCTAssertNotNil(summary.itemDate)
    }

    func testUpdatingPreservesDateDerivedFields() {
        let summary = makeSummary(
            createdAt: "2026-03-18T05:00:00Z",
            processedAt: "2026-03-18T06:00:00Z",
            publicationDate: "2026-03-17T23:30:00Z"
        )

        let updated = summary.updating(isRead: true)

        XCTAssertEqual(updated.calendarDayKey, summary.calendarDayKey)
        XCTAssertEqual(updated.relativeTimeDisplay, summary.relativeTimeDisplay)
    }

    func testDecodesSavedSourceForBookmarkFilters() throws {
        let data = try makeWireSummaryPayload(overrides: [
            "content_type": "article",
            "is_saved_to_knowledge": true,
            "saved_source": "x_bookmark",
        ])

        let summary = try JSONDecoder().decode(ContentSummary.self, from: data)

        XCTAssertEqual(summary.savedSource, "x_bookmark")
        XCTAssertTrue(summary.isXBookmark)
        XCTAssertEqual(summary.updating(isRead: true).savedSource, "x_bookmark")
    }

    func testKnowledgeSourceLabelsUseSpecificSource() {
        let summary = makeSummary(source: "arxiv.org")

        XCTAssertEqual(summary.knowledgeSourceLabels, ["arxiv.org"])
    }

    func testKnowledgeSourceLabelsReplaceSelfSubmissionWithURLHost() {
        let summary = makeSummary(
            url: "https://www.example.com/research/paper",
            source: "self submission"
        )

        XCTAssertEqual(summary.knowledgeSourceLabels, ["example.com"])
    }

    func testKnowledgeSourceLabelsKeepXOriginAndSpecificPublisher() {
        let summary = makeSummary(source: "Example Publisher", savedSource: "x_bookmark")

        XCTAssertEqual(summary.knowledgeSourceLabels, ["X Bookmark", "Example Publisher"])
    }

    func testKnowledgeSourceLabelsDoNotAddGenericSourceToXOrigin() {
        let summary = makeSummary(source: "self submission", savedSource: "x_bookmark")

        XCTAssertEqual(summary.knowledgeSourceLabels, ["X Bookmark"])
    }

    func testNewsSummaryPayloadStillDecodesButIsHiddenFromDisplaySummary() throws {
        let data = try makeWireSummaryPayload(overrides: [
            "short_summary": "Hidden list summary",
            "news_summary": "Hidden news summary",
        ])

        let summary = try JSONDecoder().decode(ContentSummary.self, from: data)

        XCTAssertEqual(summary.shortSummary, "Hidden list summary")
        XCTAssertEqual(summary.newsSummary, "Hidden news summary")
        XCTAssertNil(summary.summaryDisplayText)
        XCTAssertNil(summary.secondaryLine)

        let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(summary)) as? [String: Any]
        XCTAssertEqual(encoded?["short_summary"] as? String, "Hidden list summary")
        XCTAssertEqual(encoded?["news_summary"] as? String, "Hidden news summary")
    }

    func testArticleSummaryStillDisplaysAsSecondaryLine() {
        let summary = makeSummary(
            contentType: .article,
            createdAt: "2026-03-18T05:00:00Z",
            processedAt: nil,
            publicationDate: nil
        )

        XCTAssertEqual(summary.summaryDisplayText, "Summary")
        XCTAssertEqual(summary.secondaryLine, "Summary")
    }

    func testArticleKeyTakeawayDecodesAndSurvivesLocalUpdates() throws {
        let data = try makeWireSummaryPayload(overrides: [
            "content_type": "article",
            "short_summary": "Current list summary",
            "key_takeaway": "  The key takeaway belongs under the title.  ",
        ])

        let summary = try JSONDecoder().decode(ContentSummary.self, from: data)
        let updated = summary.updating(isRead: true)
        let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(updated)) as? [String: Any]

        XCTAssertEqual(summary.keyTakeaway, "  The key takeaway belongs under the title.  ")
        XCTAssertEqual(summary.keyTakeawayDisplayText, "The key takeaway belongs under the title.")
        XCTAssertEqual(updated.keyTakeaway, summary.keyTakeaway)
        XCTAssertEqual(encoded?["key_takeaway"] as? String, summary.keyTakeaway)
    }

    func testSavedLibraryStateTreatsNonterminalStatusesAsProcessing() {
        for status: APIContentStatus in [.new, .pending, .processing, .awaiting_image] {
            XCTAssertEqual(makeSummary(status: status).savedLibraryItemState, .processing)
        }
    }

    func testSavedLibraryStateOnlyMakesCompletedContentReady() {
        XCTAssertEqual(makeSummary(status: .completed).savedLibraryItemState, .ready)
        XCTAssertEqual(makeSummary(status: .failed).savedLibraryItemState, .unavailable)
        XCTAssertEqual(makeSummary(status: .skipped).savedLibraryItemState, .unavailable)
        XCTAssertEqual(
            makeSummary(status: .unknown("future_status")).savedLibraryItemState,
            .unavailable
        )
    }

    private func makeSummary(
        contentType: APIContentType = .news,
        status: APIContentStatus = .completed,
        url: String = "https://example.com/story",
        source: String? = "Example",
        platform: String? = "Hacker News",
        savedSource: String? = nil,
        createdAt: String = "2026-03-18T05:00:00Z",
        processedAt: String? = nil,
        publicationDate: String? = nil,
        shortSummary: String? = "Summary",
        newsSummary: String? = nil,
        keyTakeaway: String? = nil
    ) -> ContentSummary {
        ContentSummary(
            id: 7,
            contentType: contentType,
            url: url,
            title: "Example story",
            source: source,
            platform: platform,
            status: status,
            shortSummary: shortSummary,
            createdAt: createdAt,
            processedAt: processedAt,
            classification: nil,
            publicationDate: publicationDate,
            isRead: false,
            isSavedToKnowledge: false,
            imageUrl: nil,
            thumbnailUrl: nil,
            primaryTopic: nil,
            topComment: nil,
            commentCount: nil,
            newsSummary: newsSummary,
            newsKeyPoints: nil,
            keyTakeaway: keyTakeaway,
            savedSource: savedSource
        )
    }

    private func makeWireSummaryPayload(overrides: [String: Any]) throws -> Data {
        var payload: [String: Any] = [
            "id": 7,
            "content_type": "news",
            "url": "https://example.com/story",
            "source_url": NSNull(),
            "discussion_url": NSNull(),
            "title": "Example story",
            "source": "Example",
            "platform": "web",
            "status": "completed",
            "short_summary": "Summary",
            "created_at": "2026-03-18T05:00:00Z",
            "processed_at": NSNull(),
            "classification": NSNull(),
            "publication_date": NSNull(),
            "is_read": false,
            "is_saved_to_knowledge": false,
            "knowledge_saved_at": NSNull(),
            "news_article_url": NSNull(),
            "news_discussion_url": NSNull(),
            "news_key_points": NSNull(),
            "news_summary": NSNull(),
            "user_status": NSNull(),
            "image_url": NSNull(),
            "thumbnail_url": NSNull(),
            "primary_topic": NSNull(),
            "top_comment": NSNull(),
            "comment_count": NSNull(),
            "feed_preview": NSNull(),
            "artifact_type": NSNull(),
            "preview_bullets": NSNull(),
            "reason_to_read": NSNull(),
            "key_takeaway": NSNull(),
            "saved_source": NSNull(),
        ]
        payload.merge(overrides) { _, replacement in replacement }
        return try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    }
}
