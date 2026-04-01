import XCTest
@testable import newsly

final class LongArticleDisplayModeTests: XCTestCase {
    func testAllCasesExposeStableTitles() {
        XCTAssertEqual(
            LongArticleDisplayMode.allCases.map(\.rawValue),
            ["narrative", "key_points", "both"]
        )
        XCTAssertEqual(
            LongArticleDisplayMode.allCases.map(\.title),
            ["Narrative", "Key Points", "Both"]
        )
    }

    func testDetailsDescribeEachModeClearly() {
        XCTAssertEqual(
            LongArticleDisplayMode.narrative.detail,
            "Show the narrative with notable quotes"
        )
        XCTAssertEqual(
            LongArticleDisplayMode.keyPoints.detail,
            "Show key points with notable quotes"
        )
        XCTAssertEqual(
            LongArticleDisplayMode.both.detail,
            "Show narrative, key points, and quotes"
        )
    }
}
