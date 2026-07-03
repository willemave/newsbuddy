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
