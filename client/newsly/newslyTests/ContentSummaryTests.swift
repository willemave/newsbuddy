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
        let data = """
        {
          "id": 7,
          "content_type": "article",
          "url": "https://example.com/story",
          "title": "Example story",
          "source": "Example",
          "platform": "web",
          "status": "completed",
          "short_summary": "Summary",
          "created_at": "2026-03-18T05:00:00Z",
          "is_read": false,
          "is_saved_to_knowledge": true,
          "saved_source": "x_bookmark"
        }
        """.data(using: .utf8)!

        let summary = try JSONDecoder().decode(ContentSummary.self, from: data)

        XCTAssertEqual(summary.savedSource, "x_bookmark")
        XCTAssertEqual(summary.updating(isRead: true).savedSource, "x_bookmark")
    }

    func testNewsSummaryPayloadStillDecodesButIsHiddenFromDisplaySummary() throws {
        let data = """
        {
          "id": 7,
          "content_type": "news",
          "url": "https://example.com/story",
          "title": "Example story",
          "source": "Example",
          "platform": "web",
          "status": "completed",
          "short_summary": "Hidden list summary",
          "news_summary": "Hidden news summary",
          "created_at": "2026-03-18T05:00:00Z",
          "is_read": false,
          "is_saved_to_knowledge": false
        }
        """.data(using: .utf8)!

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
        let data = """
        {
          "id": 7,
          "content_type": "article",
          "url": "https://example.com/story",
          "title": "Example story",
          "source": "Example",
          "platform": "web",
          "status": "completed",
          "short_summary": "Current list summary",
          "key_takeaway": "  The key takeaway belongs under the title.  ",
          "created_at": "2026-03-18T05:00:00Z",
          "is_read": false,
          "is_saved_to_knowledge": false
        }
        """.data(using: .utf8)!

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
            url: "https://example.com/story",
            title: "Example story",
            source: "Example",
            platform: "Hacker News",
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
            keyTakeaway: keyTakeaway
        )
    }
}
