import XCTest
@testable import newsly

final class BriefingSentenceRangeTests: XCTestCase {
    func testSentenceRangeSelectsEnclosingSentenceWithoutTrailingWhitespace() {
        let text = "First sentence here. Second sentence lives after. Third."
        let index = (text as NSString).range(of: "Second").location + 2

        let range = BriefingPassageView.Coordinator.sentenceRange(around: index, in: text)

        XCTAssertEqual((text as NSString).substring(with: range), "Second sentence lives after.")
    }

    func testSentenceRangeReturnsEmptyForEmptyString() {
        let range = BriefingPassageView.Coordinator.sentenceRange(around: 0, in: "")
        XCTAssertEqual(range.length, 0)
    }
}

final class BriefingPassageTypographyTests: XCTestCase {
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
    func testFeaturePassagesUseBodyTypography() throws {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .text, text: "Feature-weight payload uses body text.")
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
                            text: "Read [Story](news://briefing/news/8) today."
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
                            text: "Read [[AINews] New AI infra decacorns](newsly://briefing/content/29576)."
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

    func testBuildAppendsClickableDiscussionIconInsideParenthesesAfterStoredMarkdownSourceLink() throws {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Read [Story](news://briefing/news/8) today."
                        )
                    ]
                )
            ],
            weight: nil,
            discussionChips: [
                "news:8": BriefingDiscussionChip(sourceKey: "news:8", commentCount: 64)
            ]
        )

        var discussionLinkRanges: [NSRange] = []
        let fullRange = NSRange(location: 0, length: result.attributedText.length)
        result.attributedText.enumerateAttribute(.link, in: fullRange) { value, range, _ in
            guard let url = value as? URL,
                  url.absoluteString == "newsly://briefing/discussion/news/8"
            else { return }
            discussionLinkRanges.append(range)
        }

        XCTAssertEqual(discussionLinkRanges.count, 1)
        XCTAssertEqual(discussionLinkRanges.first?.length, 1)
        let countRange = (result.plainText as NSString).range(of: "64")
        XCTAssertEqual(
            result.attributedText.attribute(
                .link,
                at: countRange.location,
                effectiveRange: nil
            ) as? URL,
            nil
        )
        XCTAssertTrue(result.plainText.contains("(\u{FFFC}\u{202F}64)"))
    }

    func testBriefingSourceLinkKeysIncludesStoredMarkdownSourceLinks() {
        let block = APIBriefingBlock(
            type: .passage,
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(
                            kind: .text,
                            text: "Read [Story](news://briefing/news/8) "
                                + "and [Article](newsly://briefing/content/42)."
                        )
                    ]
                )
            ]
        )

        XCTAssertEqual(block.briefingSourceLinkKeys, ["news:8", "content:42"])
    }

    func testBuildAppendsClickableDiscussionIconInsideParenthesesAfterFirstSourceLinkOnly() throws {
        let builder = BriefingAttributedTextBuilder()
        let linkRun = APIBriefingRun(
            kind: .source_link,
            text: "Read this",
            sourceKey: "news:7"
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

        var discussionLinkRanges: [NSRange] = []
        let fullRange = NSRange(location: 0, length: result.attributedText.length)
        result.attributedText.enumerateAttribute(.link, in: fullRange) { value, range, _ in
            guard let url = value as? URL,
                  url.absoluteString == "newsly://briefing/discussion/news/7"
            else { return }
            discussionLinkRanges.append(range)
        }

        XCTAssertEqual(discussionLinkRanges.count, 1, "Only the first discussion icon should be linked")
        XCTAssertEqual(discussionLinkRanges.first?.length, 1)
        let countRange = (result.plainText as NSString).range(of: "128")
        XCTAssertEqual(
            result.attributedText.attribute(
                .link,
                at: countRange.location,
                effectiveRange: nil
            ) as? URL,
            nil
        )
        XCTAssertTrue(result.plainText.contains("(\u{FFFC}\u{202F}128)"))
    }

    func testBuildStripsLeftoverBoldMarkersAroundSourceLinks() {
        let builder = BriefingAttributedTextBuilder()
        let result = builder.build(
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .text, text: "**"),
                        APIBriefingRun(kind: .source_link, text: "Linear Digressions", sourceKey: "content:7"),
                        APIBriefingRun(kind: .text, text: "** is going quiet, but 2 ** 3 stays.")
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
                    paragraphs: [
                        APIBriefingParagraph(runs: [
                            APIBriefingRun(
                                kind: .source_link,
                                text: "Story",
                                sourceKey: "news:2"
                            )
                        ])
                    ]
                )
            ],
            sourceKeys: ["news:2"]
        )
        let source = APIBriefingSource(
            sourceKey: "news:2",
            kind: "news",
            id: 2,
            title: "Story",
            read: true,
            discussion: APIBriefingDiscussion(
                platform: "hackernews",
                commentCount: 17,
                summaryStatus: "completed"
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
