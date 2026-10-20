import XCTest
@testable import newsly

final class BriefingPageInsetsTests: XCTestCase {
    func testMarginAndBarTogetherEqualLayoutTop() {
        let insets = briefingPageInsets(topContentInset: 210, readBoundaryY: 96, screenEdgeTop: 62)
        XCTAssertEqual(insets.layoutTop, 272)
        XCTAssertEqual(insets.scrollEdgeBar, 158)
        XCTAssertEqual(insets.contentMarginTop + insets.scrollEdgeBar, insets.layoutTop)
    }

    func testReadOffsetShiftIsTheStatusBar() {
        let insets = briefingPageInsets(topContentInset: 210, readBoundaryY: 96, screenEdgeTop: 62)
        XCTAssertEqual(insets.readOffsetShift, 62)
        // At rest the raw offset is -layoutTop; shifted it matches the old -topContentInset.
        XCTAssertEqual(-insets.layoutTop + insets.readOffsetShift, -210)
    }

    func testMissingOrNegativeBoundaryCoversOnlyTheStatusBar() {
        XCTAssertEqual(
            briefingPageInsets(topContentInset: 210, readBoundaryY: nil, screenEdgeTop: 62).scrollEdgeBar,
            62
        )
        XCTAssertEqual(
            briefingPageInsets(topContentInset: 210, readBoundaryY: -5, screenEdgeTop: 62).scrollEdgeBar,
            62
        )
    }

    func testBoundaryBeyondExpandedChromeIsCapped() {
        let insets = briefingPageInsets(topContentInset: 120, readBoundaryY: 400, screenEdgeTop: 62)
        XCTAssertEqual(insets.scrollEdgeBar, 182)
        XCTAssertEqual(insets.contentMarginTop, 0)
    }

    func testStandalonePageWithoutScreenEdgesKeepsOldInsets() {
        let insets = briefingPageInsets(topContentInset: 210, readBoundaryY: 96, screenEdgeTop: 0)
        XCTAssertEqual(insets.layoutTop, 210)
        XCTAssertEqual(insets.readOffsetShift, 0)
    }
}
