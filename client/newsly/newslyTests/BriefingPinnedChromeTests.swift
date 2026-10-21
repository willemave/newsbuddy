import XCTest
@testable import newsly

final class BriefingPinnedChromeTests: XCTestCase {
    private let masthead: CGFloat = 110
    private let pinned: CGFloat = 96
    private var deep: CGFloat { masthead + pinned + 200 }

    private func step(
        hidden: Bool = false,
        travel: CGFloat = 0,
        from last: CGFloat?,
        to scrolled: CGFloat
    ) -> BriefingPinnedChromeStep {
        briefingPinnedChromeStep(
            hidden: hidden,
            travel: travel,
            lastScrolled: last,
            scrolled: scrolled,
            mastheadHeight: masthead,
            pinnedHeight: pinned
        )
    }

    func testChromeStaysUntilTextIsUnderIt() {
        XCTAssertFalse(step(from: 100, to: 200).hidden, "masthead and chrome still inset the text")
    }

    func testScrollingDownDeepEnoughHides() {
        let first = step(from: deep, to: deep + 12)
        XCTAssertFalse(first.hidden, "under the hide distance")
        XCTAssertTrue(step(travel: first.travel, from: deep + 12, to: deep + 30).hidden)
    }

    func testSmallUpwardScrollReveals() {
        let jitter = step(hidden: true, travel: 40, from: deep, to: deep - 5)
        XCTAssertTrue(jitter.hidden, "jitter under the reveal distance is ignored")
        XCTAssertFalse(step(hidden: true, travel: jitter.travel, from: deep - 5, to: deep - 10).hidden)
    }

    func testDirectionChangeRestartsTravel() {
        let up = step(hidden: false, travel: 20, from: deep, to: deep - 2)
        XCTAssertEqual(up.travel, -2)
        XCTAssertFalse(step(hidden: false, travel: up.travel, from: deep - 2, to: deep + 20).hidden,
                       "down travel restarts after an upward move")
    }

    func testReturningNearTheTopAlwaysShows() {
        XCTAssertFalse(step(hidden: true, travel: 300, from: deep, to: masthead + 50).hidden)
    }

    func testFirstSampleOnlySetsBaseline() {
        XCTAssertFalse(step(from: nil, to: 3000).hidden)
    }
}
