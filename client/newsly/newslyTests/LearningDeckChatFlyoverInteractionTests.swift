import CoreGraphics
import XCTest
@testable import newsly

final class LearningDeckChatFlyoverInteractionTests: XCTestCase {
    func testUpwardVerticalSwipeExpandsChat() {
        XCTAssertTrue(
            LearningDeckChatFlyoverInteraction.shouldExpand(
                for: CGSize(width: 4, height: -36)
            )
        )
    }

    func testShortOrHorizontalDragDoesNotExpandChat() {
        XCTAssertFalse(
            LearningDeckChatFlyoverInteraction.shouldExpand(
                for: CGSize(width: 2, height: -12)
            )
        )
        XCTAssertFalse(
            LearningDeckChatFlyoverInteraction.shouldExpand(
                for: CGSize(width: 40, height: -28)
            )
        )
    }
}
