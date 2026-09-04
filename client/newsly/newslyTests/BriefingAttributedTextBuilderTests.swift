import XCTest
@testable import newsly

final class BriefingPassageCoordinatorTests: XCTestCase {
    func testBriefingLinksRouteInsideTheApp() throws {
        var openedSourceKey: String?
        var openedDiscussionKey: String?
        let coordinator = BriefingPassageView.Coordinator(
            onOpenSource: { openedSourceKey = $0 },
            onOpenDiscussion: { openedDiscussionKey = $0 }
        )
        coordinator.openBriefingLink(
            try XCTUnwrap(URL(string: "newsly://briefing/content/42"))
        )
        coordinator.openBriefingLink(try XCTUnwrap(
            URL(string: "newsly://briefing/discussion/news/8")
        ))

        XCTAssertEqual(openedSourceKey, "content:42")
        XCTAssertEqual(openedDiscussionKey, "news:8")
    }

    func testNonBriefingLinkDoesNotRouteInsideTheApp() throws {
        var callbackCount = 0
        let coordinator = BriefingPassageView.Coordinator(
            onOpenSource: { _ in callbackCount += 1 },
            onOpenDiscussion: { _ in callbackCount += 1 }
        )

        coordinator.openBriefingLink(
            try XCTUnwrap(URL(string: "https://example.com/story"))
        )

        XCTAssertEqual(callbackCount, 0)
    }
}

final class BriefingPassageTypographyTests: XCTestCase {
    func testCoordinatorScalesOncePerContentAndTraitRevision() throws {
        let coordinator = BriefingPassageView.Coordinator(
            onOpenSource: { _ in },
            onOpenDiscussion: { _ in }
        )
        let attributedText = NSAttributedString(
            string: "Briefing body",
            attributes: [.font: UIFont.appSans(size: 16)]
        )
        let largeTraits = UITraitCollection(preferredContentSizeCategory: .large)
        let accessibilityTraits = UITraitCollection(
            preferredContentSizeCategory: .accessibilityLarge
        )

        XCTAssertNotNil(
            coordinator.scaledTextIfNeeded(attributedText, compatibleWith: largeTraits)
        )
        XCTAssertNil(
            coordinator.scaledTextIfNeeded(attributedText, compatibleWith: largeTraits)
        )
        XCTAssertNotNil(
            coordinator.scaledTextIfNeeded(attributedText, compatibleWith: accessibilityTraits)
        )
    }

    func testPassageTypographyScalesWithContentTextSize() throws {
        let baseFont = UIFont.appSans(size: 16)
        let attributedText = NSAttributedString(
            string: "Briefing body",
            attributes: [.font: baseFont]
        )
        let traitCollection = UITraitCollection(preferredContentSizeCategory: .extraLarge)

        let scaledText = BriefingPassageView.scaledAttributedText(
            attributedText,
            compatibleWith: traitCollection
        )

        let scaledFont = try XCTUnwrap(
            scaledText.attribute(.font, at: 0, effectiveRange: nil) as? UIFont
        )
        let expectedFont = UIFontMetrics(forTextStyle: .callout).scaledFont(
            for: baseFont,
            compatibleWith: traitCollection
        )
        XCTAssertEqual(scaledFont.pointSize, expectedFont.pointSize, accuracy: 0.01)
        XCTAssertGreaterThan(scaledFont.pointSize, baseFont.pointSize)
    }
}

final class TypographyScalingTests: XCTestCase {
    func testSelectableMarkdownRenderCacheReusesRenderedText() {
        let cache = SelectableMarkdownRenderCache()
        let key = SelectableMarkdownView.RenderKey(
            markdown: "**Cached** markdown",
            baseFontName: "Lato-Regular",
            baseFontSize: 16,
            textColorSignature: "text",
            linkColorSignature: "link",
            colorSchemeSignature: "light"
        )
        let rendered = NSAttributedString(string: "Cached markdown")

        cache.insert(rendered, for: key)

        XCTAssertTrue(cache.value(for: key) === rendered)
    }

    func testSelectableMarkdownUsesItsSemanticTextStyleWhenScaling() {
        let baseFont = UIFont.appSans(size: 16)
        let traitCollection = UITraitCollection(preferredContentSizeCategory: .extraLarge)

        let scaledFont = SelectableMarkdownView.scaledFont(
            baseFont,
            relativeTo: .callout,
            compatibleWith: traitCollection,
            adjustsForContentSizeCategory: true
        )
        let expectedFont = UIFontMetrics(forTextStyle: .callout).scaledFont(
            for: baseFont,
            compatibleWith: traitCollection
        )

        XCTAssertEqual(scaledFont.pointSize, expectedFont.pointSize, accuracy: 0.01)
        XCTAssertGreaterThan(scaledFont.pointSize, baseFont.pointSize)
    }

    func testSemanticUIFontAppliesDynamicTypeExactlyOnce() {
        let traitCollection = UITraitCollection(preferredContentSizeCategory: .extraLarge)
        let baseFont = UIFont.appSans(size: 16)

        let scaledFont = UIFont.appSans(
            textStyle: .callout,
            compatibleWith: traitCollection
        )
        let expectedFont = UIFontMetrics(forTextStyle: .callout).scaledFont(
            for: baseFont,
            compatibleWith: traitCollection
        )

        XCTAssertEqual(scaledFont.pointSize, expectedFont.pointSize, accuracy: 0.01)
    }
}

final class BriefingAttributedTextBuilderTests: XCTestCase {
    func testPassagesUseTheCanonicalReaderBodyColor() throws {
        let result = BriefingAttributedTextBuilder().build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Briefing body",
                            sourceKey: nil,
                            insightId: nil
                        )
                    ]
                )
            ],
            weight: nil
        )
        let actual = try XCTUnwrap(
            result.attributedText.attribute(.foregroundColor, at: 0, effectiveRange: nil)
                as? UIColor
        )

        for style in [UIUserInterfaceStyle.light, .dark] {
            let traits = UITraitCollection(userInterfaceStyle: style)
            XCTAssertTrue(
                actual.resolvedColor(with: traits).isEqual(
                    UIColor.appReaderBodyText.resolvedColor(with: traits)
                )
            )
        }
    }

    func testFeaturePassagesUseBodyTypography() throws {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Feature-weight payload uses body text.",
                            sourceKey: nil,
                            insightId: nil
                        )
                    ]
                )
            ],
            weight: "feature"
        )

        let font = try XCTUnwrap(
            result.attributedText.attribute(.font, at: 0, effectiveRange: nil) as? UIFont
        )
        XCTAssertEqual(font.pointSize, 16, accuracy: 0.01)
        XCTAssertTrue(font.fontName.localizedCaseInsensitiveContains("Lato"))

        let paragraphStyle = try XCTUnwrap(
            result.attributedText.attribute(.paragraphStyle, at: 0, effectiveRange: nil)
                as? NSParagraphStyle
        )
        XCTAssertEqual(paragraphStyle.lineSpacing, 2, accuracy: 0.01)
    }

    func testBuildCreatesSourceLinksAndPlainText() throws {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .source_link,
                            text: "Read this",
                            sourceKey: "content:42",
                            insightId: nil,
                            bold: true
                        )
                    ]
                )
            ],
            weight: "feature"
        )

        XCTAssertEqual(result.plainText, "Read this")
        let link = try XCTUnwrap(
            result.attributedText.attribute(.link, at: 0, effectiveRange: nil) as? URL
        )
        XCTAssertEqual(link.absoluteString, "newsly://briefing/content/42")
    }

    func testBuildConvertsStoredMarkdownSourceLinksInTextRuns() throws {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Read [Story](news://briefing/news/8) today.",
                            sourceKey: nil,
                            insightId: nil
                        )
                    ]
                )
            ],
            weight: nil
        )

        XCTAssertEqual(result.plainText, "Read Story today.")
        let storyRange = (result.attributedText.string as NSString).range(of: "Story")
        let link = try XCTUnwrap(
            result.attributedText.attribute(.link, at: storyRange.location, effectiveRange: nil)
                as? URL
        )
        XCTAssertEqual(link.absoluteString, "newsly://briefing/news/8")
    }

    func testBuildConvertsStoredMarkdownSourceLinkWhoseTitleContainsBrackets() throws {
        let result = BriefingAttributedTextBuilder().build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Read [[AINews] New AI infra decacorns](newsly://briefing/content/29576).",
                            sourceKey: nil,
                            insightId: nil
                        )
                    ]
                )
            ],
            weight: nil
        )

        XCTAssertEqual(result.plainText, "Read [AINews] New AI infra decacorns.")
        let titleRange = (result.attributedText.string as NSString).range(of: "[AINews]")
        let link = try XCTUnwrap(
            result.attributedText.attribute(.link, at: titleRange.location, effectiveRange: nil) as? URL
        )
        XCTAssertEqual(link.absoluteString, "newsly://briefing/content/29576")
        XCTAssertEqual(
            BriefingAttributedTextBuilder.sourceKeys(
                in: "[[AINews] New AI infra decacorns](newsly://briefing/content/29576)"
            ),
            ["content:29576"]
        )
    }

    func testBuildAppendsClickableDiscussionBadgeAfterStoredMarkdownSourceLink() {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Read [Story](news://briefing/news/8) today.",
                            sourceKey: nil,
                            insightId: nil
                        )
                    ]
                )
            ],
            weight: nil,
            discussionChips: [
                "news:8": BriefingDiscussionChip(sourceKey: "news:8", commentCount: 64)
            ]
        )

        let badgeRange = (result.plainText as NSString).range(of: "(\u{FFFC}\u{202F}64)")
        XCTAssertEqual(
            linkRanges(
                in: result.attributedText,
                matching: "newsly://briefing/discussion/news/8"
            ),
            [badgeRange]
        )
    }

    func testBriefingSourceLinkKeysIncludesStoredMarkdownSourceLinks() {
        let block = APIBriefingBlock(
            type: .passage,
            weight: nil,
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Read [Story](news://briefing/news/8) "
                                + "and [Article](newsly://briefing/content/42).",
                            sourceKey: nil,
                            insightId: nil
                        )
                    ]
                )
            ],
            sourceKey: nil,
            imageUrl: nil,
            thumbnailUrl: nil,
            caption: nil,
            placement: nil,
            alignment: nil,
            text: nil
        )

        XCTAssertEqual(block.briefingSourceLinkKeys, ["news:8", "content:42"])
    }

    func testBuildAppendsClickableDiscussionBadgeAfterFirstSourceLinkOnly() {
        let builder = BriefingAttributedTextBuilder()
        let linkRun = APIBriefingRun(
            kind: .source_link,
            text: "Read this",
            sourceKey: "news:7",
            insightId: nil
        )
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(runs: [linkRun]),
                APIBriefingParagraph(runs: [linkRun])
            ],
            weight: nil,
            discussionChips: [
                "news:7": BriefingDiscussionChip(sourceKey: "news:7", commentCount: 128)
            ]
        )

        let badgeRange = (result.plainText as NSString).range(of: "(\u{FFFC}\u{202F}128)")
        XCTAssertEqual(
            linkRanges(
                in: result.attributedText,
                matching: "newsly://briefing/discussion/news/7"
            ),
            [badgeRange],
            "Only the first badge should be linked"
        )
    }

    func testBuildStripsLeftoverBoldMarkersAroundSourceLinks() {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "**",
                            sourceKey: nil,
                            insightId: nil
                        ),
                        APIBriefingRun(
                            kind: .source_link,
                            text: "Linear Digressions",
                            sourceKey: "content:7",
                            insightId: nil
                        ),
                        APIBriefingRun(
                            kind: .text,
                            text: "** is going quiet, but 2 ** 3 stays.",
                            sourceKey: nil,
                            insightId: nil
                        )
                    ]
                )
            ],
            weight: nil
        )

        XCTAssertEqual(
            result.plainText,
            "Linear Digressions is going quiet, but 2 ** 3 stays."
        )
    }

    func testCompactCountFormatsThousands() {
        XCTAssertEqual(BriefingAttributedTextBuilder.compactCount(999), "999")
        XCTAssertEqual(BriefingAttributedTextBuilder.compactCount(1000), "1k")
        XCTAssertEqual(BriefingAttributedTextBuilder.compactCount(1400), "1.4k")
        XCTAssertEqual(BriefingAttributedTextBuilder.compactCount(12345), "12k")
    }

    func testBuildRendersInsightRunsAsPlainBodyText() throws {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .insight,
                            text: "important context",
                            sourceKey: nil,
                            insightId: "source_0"
                        )
                    ]
                )
            ],
            weight: nil
        )

        XCTAssertEqual(result.plainText, "important context")
        XCTAssertNil(
            result.attributedText.attribute(.underlineStyle, at: 0, effectiveRange: nil)
        )
        XCTAssertNil(
            result.attributedText.attribute(.underlineColor, at: 0, effectiveRange: nil)
        )
    }
}

private func linkRanges(
    in attributedText: NSAttributedString,
    matching absoluteURL: String
) -> [NSRange] {
    var ranges: [NSRange] = []
    let fullRange = NSRange(location: 0, length: attributedText.length)
    attributedText.enumerateAttribute(.link, in: fullRange) { value, range, _ in
        guard (value as? URL)?.absoluteString == absoluteURL else { return }
        ranges.append(range)
    }
    return ranges
}

final class BriefingRenderModelTests: XCTestCase {
    func testLensRenderModelPrecomputesSegmentPresentationAndReadState() throws {
        let segment = APIBriefingSegment(
            id: 44,
            createdAt: Date(timeIntervalSince1970: 1_800_000_100),
            status: "active",
            narrationText: "Narration",
            blocks: [
                APIBriefingBlock(
                    type: .passage,
                    weight: nil,
                    paragraphs: [
                        APIBriefingParagraph(runs: [
                            APIBriefingRun(
                                kind: .source_link,
                                text: "Story",
                                sourceKey: "news:2",
                                insightId: nil
                            )
                        ])
                    ],
                    sourceKey: nil,
                    imageUrl: nil,
                    thumbnailUrl: nil,
                    caption: nil,
                    placement: nil,
                    alignment: nil,
                    text: nil
                )
            ],
            sourceKeys: ["news:2"]
        )
        let source = APIBriefingSource(
            sourceKey: "news:2",
            kind: "news",
            id: 2,
            title: "Story",
            summary: nil,
            keyPoints: nil,
            url: nil,
            imageUrl: nil,
            thumbnailUrl: nil,
            publishedAt: nil,
            contentType: nil,
            read: true,
            discussion: APIBriefingDiscussion(
                platform: "hackernews",
                commentCount: 17,
                summaryStatus: "completed",
                overview: nil,
                topCommentAuthor: nil,
                topCommentText: nil,
                externalUrl: nil,
                updatedAt: nil
            )
        )
        let lens = makeLens(
            key: "today",
            segments: [segment],
            sources: [source]
        )

        let renderModel = BriefingLensRenderModel(lens: lens)
        let segmentModel = try XCTUnwrap(renderModel.segments.first)

        XCTAssertEqual(segmentModel.id, 44)
        XCTAssertTrue(segmentModel.allSourcesRead)
        XCTAssertEqual(segmentModel.displayBlocks.count, 1)
        XCTAssertEqual(
            segmentModel.discussionChipsByBlockIndex[0]?["news:2"]?.commentCount,
            17
        )
        let passageContent = try XCTUnwrap(segmentModel.passageContentByBlockIndex[0])
        XCTAssertTrue(passageContent.plainText.contains("Story"))
        XCTAssertEqual(segmentModel.sourcesByKey["news:2"]?.title, "Story")
    }

    func testReadUpdateRebuildsOnlyAffectedSegmentPresentation() throws {
        let first = makeSegment(id: 10, sourceKeys: ["content:1"])
        let second = makeSegment(id: 9, sourceKeys: ["news:2"])
        let initial = makeLens(key: "today", segments: [first, second])
        let initialModel = BriefingLensRenderModel(lens: initial)
        let updated = makeLens(
            key: "today",
            segments: [first, second],
            sources: initial.sources.map { source in
                guard source.sourceKey == "content:1" else { return source }
                return APIBriefingSource(
                    sourceKey: source.sourceKey,
                    kind: source.kind,
                    id: source.id,
                    title: source.title,
                    summary: source.summary,
                    keyPoints: source.keyPoints,
                    url: source.url,
                    imageUrl: source.imageUrl,
                    thumbnailUrl: source.thumbnailUrl,
                    publishedAt: source.publishedAt,
                    contentType: source.contentType,
                    read: true,
                    discussion: source.discussion
                )
            }
        )

        let updatedModel = BriefingLensRenderModel(
            lens: updated,
            reusing: initialModel,
            affectedSourceKeys: ["content:1"]
        )

        XCTAssertFalse(initialModel.segments[0] === updatedModel.segments[0])
        XCTAssertTrue(initialModel.segments[1] === updatedModel.segments[1])
        XCTAssertTrue(updatedModel.segments[0].allSourcesRead)
        XCTAssertFalse(updatedModel.segments[1].allSourcesRead)
    }

    func testPageAppendReusesExistingSegmentPresentation() throws {
        let first = makeSegment(id: 10, sourceKeys: ["content:1"])
        let second = makeSegment(id: 9, sourceKeys: ["news:2"])
        let initial = makeLens(key: "today", segments: [first], hasMore: true)
        let initialModel = BriefingLensRenderModel(lens: initial)
        let appended = makeLens(key: "today", segments: [first, second])

        let appendedModel = BriefingLensRenderModel(
            lens: appended,
            reusing: initialModel,
            affectedSourceKeys: []
        )

        XCTAssertTrue(initialModel.segments[0] === appendedModel.segments[0])
        XCTAssertEqual(appendedModel.segments.map(\.id), [10, 9])
        XCTAssertFalse(appendedModel.hasMore)
    }
}
