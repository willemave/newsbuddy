import XCTest
@testable import newsly

@MainActor
final class RootTabBarMinimizerTests: XCTestCase {
    func testScrollingDownPastThresholdMinimizes() {
        let minimizer = RootTabBarMinimizer()
        minimizer.record(offset: 100, source: "a")
        minimizer.record(offset: 120, source: "a")
        XCTAssertFalse(minimizer.isMinimized)
        minimizer.record(offset: 140, source: "a")
        XCTAssertTrue(minimizer.isMinimized)
    }

    func testScrollingUpRestoresAfterShortTravel() {
        let minimizer = RootTabBarMinimizer()
        minimizer.record(offset: 100, source: "a")
        minimizer.record(offset: 200, source: "a")
        XCTAssertTrue(minimizer.isMinimized)
        minimizer.record(offset: 190, source: "a")
        XCTAssertTrue(minimizer.isMinimized)
        minimizer.record(offset: 180, source: "a")
        XCTAssertFalse(minimizer.isMinimized)
    }

    func testDirectionChangeResetsTravel() {
        let minimizer = RootTabBarMinimizer()
        minimizer.record(offset: 100, source: "a")
        minimizer.record(offset: 130, source: "a")
        minimizer.record(offset: 120, source: "a")
        minimizer.record(offset: 150, source: "a")
        XCTAssertFalse(minimizer.isMinimized, "Down travel restarts after an upward move")
    }

    func testNearTopAlwaysShowsFullBar() {
        let minimizer = RootTabBarMinimizer()
        minimizer.record(offset: 100, source: "a")
        minimizer.record(offset: 300, source: "a")
        XCTAssertTrue(minimizer.isMinimized)
        minimizer.record(offset: 10, source: "b")
        XCTAssertFalse(minimizer.isMinimized)
    }

    func testFirstSampleFromNewSourceOnlySetsBaseline() {
        let minimizer = RootTabBarMinimizer()
        minimizer.record(offset: 100, source: "a")
        minimizer.record(offset: 900, source: "b")
        XCTAssertFalse(minimizer.isMinimized, "Switching pages must not read as a scroll")
    }

    func testExpandClearsMinimizedState() {
        let minimizer = RootTabBarMinimizer()
        minimizer.record(offset: 100, source: "a")
        minimizer.record(offset: 300, source: "a")
        minimizer.expand()
        XCTAssertFalse(minimizer.isMinimized)
        minimizer.record(offset: 320, source: "a")
        XCTAssertFalse(minimizer.isMinimized, "Travel restarts after an explicit expand")
    }
}

@MainActor
final class RootTabBarMinimizerDistanceTests: XCTestCase {
    func testDistanceIsMeasuredFromTheTopOfContent() {
        XCTAssertEqual(
            RootTabBarMinimizer.scrolledDistance(
                contentOffsetY: -272 + 100, insetTop: 272, contentHeight: 2000, visibleHeight: 500
            ),
            100
        )
    }

    func testOverscrollIsClampedAtBothEnds() {
        XCTAssertEqual(
            RootTabBarMinimizer.scrolledDistance(
                contentOffsetY: -300, insetTop: 272, contentHeight: 2000, visibleHeight: 500
            ),
            0
        )
        XCTAssertEqual(
            RootTabBarMinimizer.scrolledDistance(
                contentOffsetY: 1700, insetTop: 272, contentHeight: 2000, visibleHeight: 500
            ),
            1500
        )
    }

    func testBounceAtTheEndDoesNotRestoreTheBar() {
        let minimizer = RootTabBarMinimizer()
        let end = RootTabBarMinimizer.scrolledDistance(
            contentOffsetY: 1600, insetTop: 0, contentHeight: 2000, visibleHeight: 500
        )
        minimizer.record(offset: end - 200, source: "a")
        minimizer.record(offset: end, source: "a")
        XCTAssertTrue(minimizer.isMinimized)
        // Rubber band past the end and back: clamped, so no upward travel.
        let overscrolled = RootTabBarMinimizer.scrolledDistance(
            contentOffsetY: 1660, insetTop: 0, contentHeight: 2000, visibleHeight: 500
        )
        minimizer.record(offset: overscrolled, source: "a")
        minimizer.record(offset: end, source: "a")
        XCTAssertTrue(minimizer.isMinimized)
    }
}
