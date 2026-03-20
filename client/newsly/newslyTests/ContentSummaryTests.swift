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

    private func makeSummary(
        createdAt: String,
        processedAt: String?,
        publicationDate: String?
    ) -> ContentSummary {
        ContentSummary(
            id: 7,
            contentType: "news",
            url: "https://example.com/story",
            title: "Example story",
            source: "Example",
            platform: "Hacker News",
            status: "completed",
            shortSummary: "Summary",
            createdAt: createdAt,
            processedAt: processedAt,
            classification: nil,
            publicationDate: publicationDate,
            isRead: false,
            isFavorited: false,
            imageUrl: nil,
            thumbnailUrl: nil,
            primaryTopic: nil,
            topComment: nil,
            commentCount: nil,
            newsSummary: nil,
            newsKeyPoints: nil
        )
    }
}
