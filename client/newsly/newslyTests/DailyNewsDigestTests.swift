//
//  DailyNewsDigestTests.swift
//  newslyTests
//

import XCTest
@testable import newsly

final class DailyNewsDigestTests: XCTestCase {
    func testCleanedSummaryTrimsWhitespace() {
        let digest = makeDigest(summary: "  Summary text.  ")

        XCTAssertEqual(digest.cleanedSummary, "Summary text.")
    }

    func testArticleCountLabelUsesArticleNoun() {
        XCTAssertEqual(makeDigest(summary: "Summary text.").articleCountLabel, "2 articles")

        let singleArticleDigest = DailyNewsDigest(
            id: 8,
            timezone: "UTC",
            title: "Digest",
            summary: "Summary text.",
            sourceCount: 1,
            groupCount: 1,
            isRead: false,
            generatedAt: "2026-03-08T18:00:00Z",
            triggerReason: "scheduler",
            windowStartAt: "2026-03-08T17:00:00Z",
            windowEndAt: "2026-03-08T18:00:00Z",
            bullets: []
        )

        XCTAssertEqual(singleArticleDigest.articleCountLabel, "1 article")
    }

    func testShowsDigDeeperActionWhenBulletsExist() {
        let digest = makeDigest(
            summary: "Summary text.",
            bullets: [
                DailyNewsDigestBulletDetail(
                    id: 1,
                    position: 1,
                    topic: "AI",
                    details: "First point",
                    sourceCount: 2
                )
            ]
        )

        XCTAssertTrue(digest.showsDigDeeperAction)
        XCTAssertEqual(digest.displayBulletDetails.map(\.cleanedText), ["First point"])
    }

    func testHidesDigDeeperActionWithoutBullets() {
        let digest = makeDigest(summary: "Summary text.")

        XCTAssertFalse(digest.showsDigDeeperAction)
        XCTAssertEqual(digest.displayBulletDetails, [])
    }

    func testDigestPreviewTextStripsTrailingCommentQuote() {
        let bullet = DailyNewsDigestBulletDetail(
            id: 1,
            position: 1,
            topic: "AI",
            details: "OpenAI shipped a new model. \"Biggest gain came from deleting work, not optimizing queries.\"",
            sourceCount: 2,
            commentQuotes: ["Biggest gain came from deleting work, not optimizing queries."]
        )

        XCTAssertEqual(
            bullet.digestPreviewText,
            "OpenAI shipped a new model."
        )
    }

    func testDigestPreviewWithSourcesAppendsTrimmedCitationTokens() {
        let bullet = DailyNewsDigestBulletDetail(
            id: 1,
            position: 1,
            topic: "AI",
            details: "OpenAI shipped a new model.",
            sourceCount: 3,
            citations: [
                DailyNewsDigestCitation(
                    newsItemId: 11,
                    label: "Hacker News",
                    title: "HN thread",
                    url: "https://news.ycombinator.com/item?id=1"
                ),
                DailyNewsDigestCitation(
                    newsItemId: 12,
                    label: nil,
                    title: "Detailed source title",
                    url: "https://www.techmeme.com/2603/p1"
                ),
                DailyNewsDigestCitation(
                    newsItemId: 13,
                    label: "Hacker News",
                    title: "Duplicate source label",
                    url: "https://news.ycombinator.com/item?id=2"
                )
            ]
        )

        XCTAssertEqual(
            bullet.digestPreviewWithSources,
            "OpenAI shipped a new model. [HN, Techmeme]"
        )
    }

    func testDigestPreviewWithSourcesUsesSubredditAndUsernameWhenAvailable() {
        let bullet = DailyNewsDigestBulletDetail(
            id: 1,
            position: 1,
            topic: "Sources",
            details: "People are arguing about the launch across social feeds.",
            sourceCount: 3,
            citations: [
                DailyNewsDigestCitation(
                    newsItemId: 21,
                    label: "Reddit",
                    title: "Reddit thread",
                    url: "https://www.reddit.com/r/apple/comments/abc123/new_launch/"
                ),
                DailyNewsDigestCitation(
                    newsItemId: 22,
                    label: "Twitter",
                    title: "X thread",
                    url: "https://x.com/sama/status/1234567890"
                ),
                DailyNewsDigestCitation(
                    newsItemId: 23,
                    label: "Hacker News",
                    title: "HN thread",
                    url: "https://news.ycombinator.com/item?id=3"
                )
            ]
        )

        XCTAssertEqual(
            bullet.digestPreviewWithSources,
            "People are arguing about the launch across social feeds. [r/apple, @sama, HN]"
        )
    }

    func testCleanedSourceLabelsDropsBlankEntriesAndDeduplicatesByLabel() {
        let digest = makeDigest(
            summary: "Summary text.",
            bullets: [
                DailyNewsDigestBulletDetail(
                    id: 1,
                    position: 1,
                    topic: "AI",
                    details: "First point",
                    sourceCount: 2,
                    citations: [
                        DailyNewsDigestCitation(
                            newsItemId: 11,
                            label: " @swyx ",
                            title: "Hacker News",
                            url: "https://news.ycombinator.com/item?id=1"
                        ),
                        DailyNewsDigestCitation(
                            newsItemId: 12,
                            label: "  ",
                            title: "Hacker News",
                            url: "https://news.ycombinator.com/item?id=2"
                        ),
                        DailyNewsDigestCitation(
                            newsItemId: 13,
                            label: "@swyx",
                            title: "Techmeme",
                            url: "https://www.techmeme.com"
                        ),
                        DailyNewsDigestCitation(
                            newsItemId: 14,
                            label: " OpenAI ",
                            title: "OpenAI",
                            url: "https://openai.com"
                        )
                    ]
                )
            ]
        )

        XCTAssertEqual(digest.cleanedSourceLabels, ["@swyx", "OpenAI"])
    }

    private func makeDigest(
        summary: String,
        bullets: [DailyNewsDigestBulletDetail] = []
    ) -> DailyNewsDigest {
        DailyNewsDigest(
            id: 7,
            timezone: "UTC",
            title: "Digest",
            summary: summary,
            sourceCount: 2,
            groupCount: bullets.count,
            isRead: false,
            generatedAt: "2026-03-08T18:00:00Z",
            triggerReason: "scheduler",
            windowStartAt: "2026-03-08T17:00:00Z",
            windowEndAt: "2026-03-08T18:00:00Z",
            bullets: bullets
        )
    }
}
