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

final class BriefingAttributedTextBuilderTests: XCTestCase {
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

    func testBuildAppendsDiscussionChipAfterFirstSourceLinkOnly() throws {
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

        XCTAssertEqual(discussionLinkRanges.count, 1, "Chip should attach to the first link only")
        XCTAssertTrue(result.plainText.contains("128"))
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

    func testBuildMarksInsightRuns() throws {
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

        let insight = try XCTUnwrap(
            result.attributedText.attribute(
                BriefingInsightAttributeName,
                at: 0,
                effectiveRange: nil
            ) as? String
        )
        XCTAssertEqual(insight, "source_0")
    }
}
