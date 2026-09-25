import XCTest
@testable import newsly

final class BriefingSegmentHeadlineTests: XCTestCase {
    func testArticleSegmentUsesTitlePublisherAndReadingTime() throws {
        let source = makeSource(
            key: "content:1",
            contentType: .article,
            title: "  Runway’s WorldPrompt  ",
            publisher: "Latent Space",
            readingMinutes: 12
        )

        let headline = try XCTUnwrap(makeHeadline(sourceKeys: ["content:1"], sources: [source]))

        XCTAssertEqual(headline.sourceKey, "content:1")
        XCTAssertEqual(headline.title, "Runway’s WorldPrompt")
        XCTAssertEqual(headline.publisher, "Latent Space")
        XCTAssertEqual(headline.meta, "Article · 12 min read")
    }

    func testPodcastSegmentUsesDurationAndOmitsBlankPublisher() throws {
        let source = makeSource(
            key: "content:2",
            contentType: .podcast,
            publisher: "  ",
            durationSeconds: 3_939
        )

        let headline = try XCTUnwrap(makeHeadline(sourceKeys: ["content:2"], sources: [source]))

        XCTAssertNil(headline.publisher)
        XCTAssertEqual(headline.meta, "Podcast · 1 hr 6 min")
    }

    func testMissingTimingFallsBackToKindOnly() throws {
        let source = makeSource(key: "content:3", contentType: .article)

        let headline = try XCTUnwrap(makeHeadline(sourceKeys: ["content:3"], sources: [source]))

        XCTAssertEqual(headline.meta, "Article")
    }

    func testNewsAndMultiSourceSegmentsHaveNoHeadline() {
        let news = makeSource(key: "news:4", kind: "news", contentType: .news)
        let first = makeSource(key: "content:5", contentType: .article)
        let second = makeSource(key: "content:6", contentType: .article)

        XCTAssertNil(makeHeadline(sourceKeys: ["news:4"], sources: [news]))
        XCTAssertNil(makeHeadline(sourceKeys: ["content:5", "content:6"], sources: [first, second]))
        XCTAssertNil(makeHeadline(sourceKeys: ["content:7"], sources: []))
    }

    func testPublisherDropsFeedTagline() {
        XCTAssertEqual(
            BriefingSegmentHeadline.publisherName("The Green Techpreneur | Climate Marketplace"),
            "The Green Techpreneur"
        )
        XCTAssertEqual(
            BriefingSegmentHeadline.publisherName("Venturing with Vishesh — Startup Founder Interviews"),
            "Venturing with Vishesh"
        )
        XCTAssertEqual(BriefingSegmentHeadline.publisherName("Moody's Talks - Inside Economics"), "Moody's Talks - Inside Economics")
        XCTAssertNil(BriefingSegmentHeadline.publisherName(" | Tagline"))
    }

    func testDurationLabelRoundsToWholeMinutes() {
        XCTAssertNil(BriefingSegmentHeadline.durationLabel(seconds: 0))
        XCTAssertEqual(BriefingSegmentHeadline.durationLabel(seconds: 20), "1 min")
        XCTAssertEqual(BriefingSegmentHeadline.durationLabel(seconds: 1_824), "30 min")
        XCTAssertEqual(BriefingSegmentHeadline.durationLabel(seconds: 3_600), "1 hr")
    }

    private func makeHeadline(
        sourceKeys: [String],
        sources: [APIBriefingSource]
    ) -> BriefingSegmentHeadline? {
        let segment = APIBriefingSegment(
            id: 1,
            createdAt: Date(timeIntervalSince1970: 1_800_000_000),
            status: "active",
            narrationText: "",
            sourceKeys: sourceKeys
        )
        return BriefingSegmentHeadline(
            segment: segment,
            sourcesByKey: Dictionary(uniqueKeysWithValues: sources.map { ($0.sourceKey, $0) })
        )
    }

    private func makeSource(
        key: String,
        kind: String = "content",
        contentType: APIContentType,
        title: String = "Title",
        publisher: String? = nil,
        durationSeconds: Int? = nil,
        readingMinutes: Int? = nil
    ) -> APIBriefingSource {
        APIBriefingSource(
            sourceKey: key,
            kind: kind,
            id: 1,
            title: title,
            publisher: publisher,
            summary: nil,
            keyPoints: nil,
            url: nil,
            imageUrl: nil,
            thumbnailUrl: nil,
            publishedAt: nil,
            contentType: contentType,
            durationSeconds: durationSeconds,
            readingMinutes: readingMinutes,
            discussion: nil
        )
    }
}
