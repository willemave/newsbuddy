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

    func testShowsDigDeeperActionWhenSummaryExistsWithoutBullets() {
        let digest = makeDigest(summary: "Summary text.", keyPoints: [], sourceCount: 3)

        XCTAssertTrue(digest.showsDigDeeperAction)
    }

    func testHidesDigDeeperActionWithoutSources() {
        let digest = makeDigest(summary: "Summary text.", keyPoints: [], sourceCount: 0)

        XCTAssertFalse(digest.showsDigDeeperAction)
    }

    func testDisplayCoverageLabelIsNilForOlderDigests() {
        let digest = makeDigest(summary: "Summary text.", keyPoints: [], coverageEndAt: "2026-03-08T18:00:00Z")

        XCTAssertNil(digest.displayCoverageLabel)
    }

    private func makeDigest(
        summary: String,
        keyPoints: [String],
        sourceCount: Int = 2,
        coverageEndAt: String? = nil
    ) -> DailyNewsDigest {
        DailyNewsDigest(
            id: 7,
            localDate: "2026-03-08",
            timezone: "UTC",
            title: "Digest",
            summary: summary,
            keyPoints: keyPoints,
            sourceCount: sourceCount,
            sourceContentIds: [11, 12],
            isRead: false,
            readAt: nil,
            generatedAt: "2026-03-08T18:00:00Z",
            coverageEndAt: coverageEndAt
        )
    }
}
