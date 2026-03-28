//
//  DailyNewsDigestTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

final class DailyNewsDigestTests: XCTestCase {
    func testCleanedSummaryTrimsWhitespace() {
        let digest = makeDigest(summary: "  Summary text.  ", keyPoints: [])

        XCTAssertEqual(digest.cleanedSummary, "Summary text.")
    }

    func testCleanedKeyPointsDropsBlankEntries() {
        let digest = makeDigest(
            summary: "Summary text.",
            keyPoints: [" First point ", "", "  ", "Second point"]
        )

        XCTAssertEqual(digest.cleanedKeyPoints, ["First point", "Second point"])
    }

    func testHidesDigDeeperActionWhenSummaryExistsWithoutBullets() {
        let digest = makeDigest(summary: "Summary text.", keyPoints: [], sourceCount: 3)

        XCTAssertFalse(digest.showsDigDeeperAction)
    }

    func testHidesDigDeeperActionWithoutSources() {
        let digest = makeDigest(summary: "Summary text.", keyPoints: [], sourceCount: 0)

        XCTAssertFalse(digest.showsDigDeeperAction)
    }

    func testShowsDigDeeperActionWhenBulletDetailsExist() {
        let digest = makeDigest(
            summary: "Summary text.",
            keyPoints: [],
            bulletDetails: [
                DailyNewsDigestBulletDetail(
                    text: " First point ",
                    sourceCount: 2,
                    citations: [],
                    commentQuotes: []
                )
            ]
        )

        XCTAssertTrue(digest.showsDigDeeperAction)
    }

    func testDisplayBulletDetailsFallsBackToCleanedKeyPoints() {
        let digest = makeDigest(
            summary: "Summary text.",
            keyPoints: [" First point ", "", "Second point "]
        )

        XCTAssertEqual(digest.displayBulletDetails.map(\.cleanedText), ["First point", "Second point"])
    }

    func testDigestPreviewTextStripsTrailingCommentQuote() {
        let bullet = DailyNewsDigestBulletDetail(
            text: "OpenAI shipped GPT-5 and developers are already testing new workflows. \"Biggest gain came from deleting work, not optimizing queries.\"",
            sourceCount: 2,
            citations: [],
            commentQuotes: ["Biggest gain came from deleting work, not optimizing queries."]
        )

        XCTAssertEqual(
            bullet.digestPreviewText,
            "OpenAI shipped GPT-5 and developers are already testing new workflows."
        )
    }

    func testCleanedSourceLabelsDropsBlankEntries() {
        let digest = makeDigest(
            summary: "Summary text.",
            keyPoints: [],
            sourceLabels: [" @swyx ", "", "Hacker News", "  "]
        )

        XCTAssertEqual(digest.cleanedSourceLabels, ["@swyx", "Hacker News"])
    }

    func testDisplayCoverageLabelIsNilForOlderDigests() {
        let digest = makeDigest(summary: "Summary text.", keyPoints: [], coverageEndAt: "2026-03-08T18:00:00Z")

        XCTAssertNil(digest.displayCoverageLabel)
    }

    private func makeDigest(
        summary: String,
        keyPoints: [String],
        sourceCount: Int = 2,
        coverageEndAt: String? = nil,
        sourceLabels: [String] = [],
        bulletDetails: [DailyNewsDigestBulletDetail] = []
    ) -> DailyNewsDigest {
        DailyNewsDigest(
            id: 7,
            localDate: "2026-03-08",
            timezone: "UTC",
            title: "Digest",
            summary: summary,
            keyPoints: keyPoints,
            bulletDetails: bulletDetails,
            sourceCount: sourceCount,
            sourceContentIds: [11, 12],
            sourceLabels: sourceLabels,
            isRead: false,
            readAt: nil,
            generatedAt: "2026-03-08T18:00:00Z",
            coverageEndAt: coverageEndAt
        )
    }
}
