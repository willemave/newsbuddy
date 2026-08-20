import CoreGraphics
import XCTest
@testable import newsly

final class LearningDeckChatFlyoverInteractionTests: XCTestCase {
    func testVerticalDragsMoveOnePresentationStepAtATime() {
        XCTAssertEqual(
            LearningDeckChatFlyoverInteraction.target(
                from: .peek,
                for: CGSize(width: 4, height: -36)
            ),
            .compact
        )
        XCTAssertEqual(
            LearningDeckChatFlyoverInteraction.target(
                from: .compact,
                for: CGSize(width: 4, height: -36)
            ),
            .focus
        )
        XCTAssertEqual(
            LearningDeckChatFlyoverInteraction.target(
                from: .focus,
                for: CGSize(width: 4, height: 36)
            ),
            .compact
        )
        XCTAssertEqual(
            LearningDeckChatFlyoverInteraction.target(
                from: .compact,
                for: CGSize(width: 4, height: 36)
            ),
            .peek
        )
    }

    func testShortOrHorizontalDragKeepsPresentation() {
        XCTAssertEqual(
            LearningDeckChatFlyoverInteraction.target(
                from: .compact,
                for: CGSize(width: 2, height: -12)
            ),
            .compact
        )
        XCTAssertEqual(
            LearningDeckChatFlyoverInteraction.target(
                from: .compact,
                for: CGSize(width: 40, height: -28)
            ),
            .compact
        )
    }

    func testFocusedHeightUsesRoughlyThreeQuartersOfScreen() {
        XCTAssertEqual(
            LearningDeckChatHeightPolicy.height(
                for: .focus,
                size: CGSize(width: 390, height: 1_000),
                isAccessibilitySize: false
            ),
            740
        )
        XCTAssertNil(
            LearningDeckChatHeightPolicy.height(
                for: .peek,
                size: CGSize(width: 390, height: 1_000),
                isAccessibilitySize: false
            )
        )
    }
}
