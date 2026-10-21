import XCTest
@testable import newsly

final class BriefingPageInsetsTests: XCTestCase {
    func testMarginAndBarTogetherEqualLayoutTop() {
        let insets = briefingPageInsets(topContentInset: 210, screenEdgeTop: 62)
        XCTAssertEqual(insets.layoutTop, 272)
        XCTAssertEqual(insets.scrollEdgeBar, 62)
        XCTAssertEqual(insets.contentMarginTop + insets.scrollEdgeBar, insets.layoutTop)
    }

    func testReadOffsetShiftIsTheStatusBar() {
        let insets = briefingPageInsets(topContentInset: 210, screenEdgeTop: 62)
        XCTAssertEqual(insets.readOffsetShift, 62)
        // At rest the raw offset is -layoutTop; shifted it matches the old -topContentInset.
        XCTAssertEqual(-insets.layoutTop + insets.readOffsetShift, -210)
    }

    func testScrollEdgeBarIgnoresChromeSize() {
        // Resizing the bar mid-scroll re-laid out the page, so it depends only
        // on the status bar.
        XCTAssertEqual(
            briefingPageInsets(topContentInset: 120, screenEdgeTop: 62).scrollEdgeBar,
            briefingPageInsets(topContentInset: 400, screenEdgeTop: 62).scrollEdgeBar
        )
    }

    func testStandalonePageWithoutScreenEdgesKeepsOldInsets() {
        let insets = briefingPageInsets(topContentInset: 210, screenEdgeTop: 0)
        XCTAssertEqual(insets.layoutTop, 210)
        XCTAssertEqual(insets.readOffsetShift, 0)
        XCTAssertEqual(insets.contentMarginTop, 210)
    }
}
