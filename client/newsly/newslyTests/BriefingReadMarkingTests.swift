import XCTest
@testable import newsly

final class BriefingReadMarkingTests: XCTestCase {
    func testBlockSourceLinkKeysPreserveOrderAndRemoveDuplicates() {
        let block = APIBriefingBlock(
            type: .passage,
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .source_link, text: "First", sourceKey: "content:1"),
                        APIBriefingRun(kind: .text, text: " body "),
                        APIBriefingRun(kind: .source_link, text: "Second", sourceKey: "news:2"),
                    ]
                ),
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .source_link, text: "First again", sourceKey: "content:1")
                    ]
                )
            ]
        )

        XCTAssertEqual(block.briefingSourceLinkKeys, ["content:1", "news:2"])
    }

    func testFallbackReadSourceKeysIncludeDirectAndInlineSourceLinks() {
        let block = APIBriefingBlock(
            type: .passage,
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .source_link, text: "Linked", sourceKey: "news:2")
                    ]
                )
            ],
            sourceKey: "content:1"
        )

        XCTAssertEqual(block.briefingFallbackReadSourceKeys, ["content:1", "news:2"])
    }
}
