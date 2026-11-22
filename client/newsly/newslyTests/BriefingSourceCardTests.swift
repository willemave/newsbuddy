import XCTest
@testable import newsly

final class BriefingSourceCardTests: XCTestCase {
    func testArticleCardUsesTitlePublisherReadingTimeAndImages() throws {
        let source = makeSource(
            key: "content:1",
            contentType: .article,
            title: "  Runway’s WorldPrompt  ",
            publisher: "Latent Space",
            readingMinutes: 12,
            imageUrl: "https://cdn.example/hero.png",
            thumbnailUrl: "https://cdn.example/thumb.png"
        )

        let card = try XCTUnwrap(makeCard(sourceKeys: ["content:1"], sources: [source]))

        XCTAssertEqual(card.sourceKey, "content:1")
        XCTAssertEqual(card.kind, .article)
        XCTAssertEqual(card.title, "Runway’s WorldPrompt")
        XCTAssertEqual(card.publisher, "Latent Space")
        XCTAssertEqual(card.length, "12 min read")
        XCTAssertEqual(card.imageURL?.absoluteString, "https://cdn.example/hero.png")
        XCTAssertEqual(card.thumbnailURL?.absoluteString, "https://cdn.example/thumb.png")
        XCTAssertEqual(card.briefedAt, Date(timeIntervalSince1970: 1_800_000_000))
    }

    func testPodcastCardUsesDurationAndOmitsBlankPublisher() throws {
        let source = makeSource(
            key: "content:2",
            contentType: .podcast,
            publisher: "  ",
            durationSeconds: 3_939
        )

        let card = try XCTUnwrap(makeCard(sourceKeys: ["content:2"], sources: [source]))

        XCTAssertEqual(card.kind, .podcast)
        XCTAssertNil(card.publisher)
        XCTAssertEqual(card.length, "1 hr 6 min")
    }

    func testUnknownOrZeroLengthHidesChip() throws {
        let unknown = makeSource(key: "content:3", contentType: .article)
        let zero = makeSource(key: "content:4", contentType: .article, readingMinutes: 0)
        let silentPodcast = makeSource(key: "content:5", contentType: .podcast, durationSeconds: 0)

        XCTAssertNil(try XCTUnwrap(makeCard(sourceKeys: ["content:3"], sources: [unknown])).length)
        XCTAssertNil(try XCTUnwrap(makeCard(sourceKeys: ["content:4"], sources: [zero])).length)
        XCTAssertNil(try XCTUnwrap(makeCard(sourceKeys: ["content:5"], sources: [silentPodcast])).length)
    }

    func testFooterShowsPublicationDateNeverBriefingDate() throws {
        let publishedAt = Date(timeIntervalSince1970: 1_680_000_000)
        let dated = makeSource(key: "content:1", contentType: .article, publishedAt: publishedAt)
        let undated = makeSource(key: "content:2", contentType: .podcast)

        let datedCard = try XCTUnwrap(makeCard(sourceKeys: ["content:1"], sources: [dated]))
        let undatedCard = try XCTUnwrap(makeCard(sourceKeys: ["content:2"], sources: [undated]))

        let publicationDate = try XCTUnwrap(datedCard.publicationDate)
        XCTAssertTrue(publicationDate.contains("2023"))
        XCTAssertEqual(datedCard.footerLine, publicationDate)
        XCTAssertNil(undatedCard.publicationDate)
        XCTAssertEqual(undatedCard.footerLine, "Podcast")
    }

    func testAccessibilityLabelCarriesEveryVisibleDetailAndBriefedTime() throws {
        let source = makeSource(
            key: "content:1",
            contentType: .article,
            title: "The Harness-Maxxing Trap",
            publisher: "LLM Watch",
            readingMinutes: 10,
            publishedAt: Date(timeIntervalSince1970: 1_680_000_000)
        )
        let card = try XCTUnwrap(makeCard(sourceKeys: ["content:1"], sources: [source]))

        let label = card.accessibilityLabel
        XCTAssertTrue(label.hasPrefix("The Harness-Maxxing Trap, LLM Watch, Article, 10 min read, Published "))
        XCTAssertTrue(label.contains(card.briefedLabel))
        XCTAssertTrue(card.briefedLabel.hasPrefix("Briefed "))
    }

    func testNewsMultiSourceAndBlankTitleSegmentsAreNotCards() {
        let news = makeSource(key: "news:4", kind: "news", contentType: .news)
        let first = makeSource(key: "content:5", contentType: .article)
        let second = makeSource(key: "content:6", contentType: .article)
        let untitled = makeSource(key: "content:8", contentType: .article, title: "   ")

        XCTAssertNil(makeCard(sourceKeys: ["news:4"], sources: [news]))
        XCTAssertNil(makeCard(sourceKeys: ["content:5", "content:6"], sources: [first, second]))
        XCTAssertNil(makeCard(sourceKeys: ["content:7"], sources: []))
        XCTAssertNil(makeCard(sourceKeys: ["content:8"], sources: [untitled]))
    }

    func testRenderModelExposesCardAndOnlyPassageBlocks() throws {
        let source = makeSource(key: "content:1", contentType: .article, readingMinutes: 4)
        let segment = APIBriefingSegment(
            id: 9,
            createdAt: Date(timeIntervalSince1970: 1_800_000_000),
            status: "active",
            narrationText: "",
            blocks: [passage("First idea."), passage("Second idea.")],
            sourceKeys: ["content:1"]
        )

        let model = BriefingSegmentRenderModel(segment: segment, sourcesByKey: ["content:1": source])

        XCTAssertEqual(model.card?.sourceKey, "content:1")
        XCTAssertEqual(model.passageBlockIndices, [0, 1])
        XCTAssertEqual(model.passageContentByBlockIndex[1]?.plainText, "Second idea.")
        XCTAssertFalse(model.allSourcesRead)
    }

    func testCardSummariesUseSmallerTypeThanNewsProse() throws {
        let card = makeSource(key: "content:1", contentType: .article)
        let news = makeSource(key: "news:2", kind: "news", contentType: .news)
        func font(for source: APIBriefingSource) throws -> UIFont {
            let segment = APIBriefingSegment(
                id: 1, createdAt: Date(), status: "active", narrationText: "",
                blocks: [passage("Summary text.")], sourceKeys: [source.sourceKey]
            )
            let model = BriefingSegmentRenderModel(segment: segment, sourcesByKey: [source.sourceKey: source])
            let text = try XCTUnwrap(model.passageContentByBlockIndex[0]?.attributedText)
            return try XCTUnwrap(text.attribute(.font, at: 0, effectiveRange: nil) as? UIFont)
        }

        XCTAssertEqual(try font(for: card).pointSize, BriefingSourceCard.summaryStyle.size)
        XCTAssertEqual(try font(for: news).pointSize, BriefingAttributedTextBuilder.BodyStyle.passage.size)
        XCTAssertLessThan(BriefingSourceCard.summaryStyle.size, BriefingAttributedTextBuilder.BodyStyle.passage.size)
    }

    func testPublisherDropsFeedTagline() {
        XCTAssertEqual(
            BriefingSourceCard.publisherName("The Green Techpreneur | Climate Marketplace"),
            "The Green Techpreneur"
        )
        XCTAssertEqual(
            BriefingSourceCard.publisherName("Venturing with Vishesh — Startup Founder Interviews"),
            "Venturing with Vishesh"
        )
        XCTAssertEqual(BriefingSourceCard.publisherName("Moody's Talks - Inside Economics"), "Moody's Talks - Inside Economics")
        XCTAssertNil(BriefingSourceCard.publisherName(" | Tagline"))
    }

    func testShortDateOmitsCurrentYear() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let now = Date(timeIntervalSince1970: 1_792_800_000) // 2026-10-24
        let sameYear = Date(timeIntervalSince1970: 1_792_108_800) // 2026-10-16
        let earlier = Date(timeIntervalSince1970: 1_680_000_000) // 2023-03-28

        let sameYearLabel = BriefingSourceCard.shortDate(sameYear, now: now, calendar: calendar)
        let earlierLabel = BriefingSourceCard.shortDate(earlier, now: now, calendar: calendar)

        XCTAssertFalse(sameYearLabel.contains("2026"))
        XCTAssertTrue(earlierLabel.contains("2023"))
    }

    func testDurationLabelRoundsToWholeMinutes() {
        XCTAssertNil(BriefingSourceCard.durationLabel(seconds: 0))
        XCTAssertEqual(BriefingSourceCard.durationLabel(seconds: 20), "1 min")
        XCTAssertEqual(BriefingSourceCard.durationLabel(seconds: 1_824), "30 min")
        XCTAssertEqual(BriefingSourceCard.durationLabel(seconds: 3_600), "1 hr")
    }

    private func makeCard(
        sourceKeys: [String],
        sources: [APIBriefingSource]
    ) -> BriefingSourceCard? {
        let segment = APIBriefingSegment(
            id: 1,
            createdAt: Date(timeIntervalSince1970: 1_800_000_000),
            status: "active",
            narrationText: "",
            sourceKeys: sourceKeys
        )
        return BriefingSourceCard(
            segment: segment,
            sourcesByKey: Dictionary(uniqueKeysWithValues: sources.map { ($0.sourceKey, $0) })
        )
    }

    private func passage(_ text: String) -> APIBriefingBlock {
        APIBriefingBlock(
            type: .passage, weight: nil,
            paragraphs: [APIBriefingParagraph(runs: [
                APIBriefingRun(kind: .text, text: text, sourceKey: nil, insightId: nil)
            ])],
            sourceKey: nil, imageUrl: nil, thumbnailUrl: nil,
            caption: nil, placement: nil, alignment: nil, text: nil
        )
    }

    private func makeSource(
        key: String,
        kind: String = "content",
        contentType: APIContentType,
        title: String = "Title",
        publisher: String? = nil,
        durationSeconds: Int? = nil,
        readingMinutes: Int? = nil,
        publishedAt: Date? = nil,
        imageUrl: String? = nil,
        thumbnailUrl: String? = nil
    ) -> APIBriefingSource {
        APIBriefingSource(
            sourceKey: key,
            kind: kind,
            id: 1,
            title: title,
            publisher: publisher,
            aggregator: nil,
            summary: nil,
            keyPoints: nil,
            url: nil,
            imageUrl: imageUrl,
            thumbnailUrl: thumbnailUrl,
            publishedAt: publishedAt,
            contentType: contentType,
            durationSeconds: durationSeconds,
            readingMinutes: readingMinutes,
            discussion: nil
        )
    }
}
