import XCTest
@testable import newsly

final class BriefingCardSwipeTests: XCTestCase {
    private func swipe(_ dx: CGFloat, _ dy: CGFloat = 0) -> BriefingCardSwipe {
        BriefingCardSwipe(translation: CGSize(width: dx, height: dy))
    }

    func testRestingCardDoesNothing() {
        XCTAssertEqual(swipe(0).phase, .idle)
        XCTAssertNil(swipe(0).release)
        XCTAssertEqual(swipe(0).cardOffset, 0)
        XCTAssertEqual(swipe(0).progress, 0)
    }

    func testRightToLeftMarksReadOnlyPastTheThreshold() {
        XCTAssertEqual(swipe(-40).phase, .markRead(armed: false))
        XCTAssertNil(swipe(-40).release)
        XCTAssertEqual(swipe(-72).phase, .markRead(armed: true))
        XCTAssertEqual(swipe(-72).release, .markRead)
        XCTAssertEqual(swipe(-72, 60).release, .markRead, "vertical drift does not matter")
    }

    func testMarkReadDragFollowsTheFingerThenResists() {
        XCTAssertEqual(swipe(-50).cardOffset, -50)
        XCTAssertEqual(swipe(-72).cardOffset, -72)
        XCTAssertLessThan(abs(swipe(-172).cardOffset), 172)
        XCTAssertGreaterThan(abs(swipe(-172).cardOffset), 72)
        XCTAssertEqual(swipe(-200).progress, 1)
    }

    func testLeftToRightPicksTheOptionTheFingerMovesToward() {
        XCTAssertEqual(swipe(80, -30).selection, .chat)
        XCTAssertEqual(swipe(80, 30).selection, .deck)
        XCTAssertNil(swipe(80, 10).selection, "a straight swipe selects nothing")
        XCTAssertNil(swipe(-80, -30).selection, "only a left-to-right drag offers options")
    }

    func testOptionsRunOnlyWhenSelectedAndPastTheThreshold() {
        XCTAssertEqual(swipe(80, -30).release, .chat)
        XCTAssertEqual(swipe(80, 30).release, .deck)
        XCTAssertEqual(swipe(30, -30).phase, .options(selection: .chat, armed: false))
        XCTAssertNil(swipe(30, -30).release, "not far enough")
        XCTAssertNil(swipe(120, 0).release, "far enough but nothing selected")
    }

    func testCardFollowsTheFingerThenResistsAndTurnsTranslucent() {
        XCTAssertEqual(swipe(40).cardOffset, 40)
        XCTAssertEqual(swipe(-40).cardOffset, -40)
        XCTAssertEqual(swipe(220).cardOffset, 150, accuracy: 0.001, "resists past the travel")
        XCTAssertEqual(swipe(28).progress, 0.5, accuracy: 0.001)
        XCTAssertEqual(swipe(0).cardOpacity, 1)
        XCTAssertEqual(swipe(-200).cardOpacity, BriefingCardSwipe.revealedCardOpacity, accuracy: 0.001)
        XCTAssertGreaterThan(swipe(28).cardOpacity, BriefingCardSwipe.revealedCardOpacity)
    }

    func testCardShrinksAsItLiftsAndReturnsFullSizeAtRest() {
        XCTAssertEqual(swipe(0).cardScale, 1)
        XCTAssertEqual(swipe(-200).cardScale, BriefingCardSwipe.liftedCardScale, accuracy: 0.001)
        XCTAssertEqual(swipe(200).cardScale, BriefingCardSwipe.liftedCardScale, accuracy: 0.001)
        let halfway = swipe(28).cardScale
        XCTAssertLessThan(halfway, 1)
        XCTAssertGreaterThan(halfway, BriefingCardSwipe.liftedCardScale)
        XCTAssertLessThan(halfway, 1 - (1 - BriefingCardSwipe.liftedCardScale) / 2, "eases out")
    }
}
