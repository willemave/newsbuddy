import XCTest
@testable import newsly

final class BriefingPinnedChromeTests: XCTestCase {
    private let masthead: CGFloat = 110
    private let pinned: CGFloat = 96

    private func step(
        hide: CGFloat = 0,
        travel: CGFloat = 0,
        from last: CGFloat?,
        to scrolled: CGFloat
    ) -> BriefingPinnedChromeStep {
        briefingPinnedChromeStep(
            hide: hide,
            upwardTravel: travel,
            lastScrolled: last,
            scrolled: scrolled,
            mastheadHeight: masthead,
            pinnedHeight: pinned
        )
    }

    func testChromeStaysWhileMastheadCollapses() {
        XCTAssertEqual(step(from: 0, to: 100).hide, 0)
    }

    func testChromeScrollsOffOneToOnePastTheMasthead() {
        XCTAssertEqual(step(from: 110, to: 150).hide, 40)
        XCTAssertEqual(step(hide: 40, from: 150, to: 400).hide, pinned, "capped at its height")
    }

    func testUpwardScrollRevealsImmediately() {
        let small = step(hide: pinned, from: 900, to: 895)
        XCTAssertEqual(small.hide, pinned, "jitter under the reveal distance is ignored")
        let reveal = step(hide: pinned, travel: small.upwardTravel, from: 895, to: 890)
        XCTAssertEqual(reveal.hide, 0)
        XCTAssertTrue(reveal.revealed)
    }

    func testScrollingDownAgainAfterRevealHidesFromZero() {
        XCTAssertEqual(step(hide: 0, from: 890, to: 920).hide, 30)
    }

    func testDownwardMoveResetsUpwardTravel() {
        XCTAssertEqual(step(hide: pinned, travel: 6, from: 900, to: 905).upwardTravel, 0)
    }

    func testNearTheTopChromeCanNeverBeHidden() {
        let result = step(hide: pinned, travel: 0, from: nil, to: 120)
        XCTAssertEqual(result.hide, 10, "hide never exceeds distance past the masthead")
    }

    func testFirstSampleOnlySetsBaseline() {
        XCTAssertEqual(step(hide: 0, from: nil, to: 2000).hide, 0)
    }

    func testSettleFinishesHidingWhenMostlyHidden() {
        XCTAssertEqual(
            briefingPinnedChromeSettleTarget(hide: 60, scrolled: 600, mastheadHeight: masthead, pinnedHeight: pinned),
            pinned
        )
    }

    func testSettleReturnsWhenMostlyShown() {
        XCTAssertEqual(
            briefingPinnedChromeSettleTarget(hide: 30, scrolled: 600, mastheadHeight: masthead, pinnedHeight: pinned),
            0
        )
    }

    func testSettleNeverHidesWhereChromeStillInsetsText() {
        XCTAssertEqual(
            briefingPinnedChromeSettleTarget(hide: 60, scrolled: 170, mastheadHeight: masthead, pinnedHeight: pinned),
            0
        )
    }

    func testSettleLeavesRestingStatesAlone() {
        XCTAssertNil(briefingPinnedChromeSettleTarget(hide: 0, scrolled: 600, mastheadHeight: masthead, pinnedHeight: pinned))
        XCTAssertNil(briefingPinnedChromeSettleTarget(hide: pinned, scrolled: 600, mastheadHeight: masthead, pinnedHeight: pinned))
    }
}
