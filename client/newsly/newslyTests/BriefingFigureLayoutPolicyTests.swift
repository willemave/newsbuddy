import SwiftUI
import XCTest
@testable import newsly

final class BriefingFigureLayoutPolicyTests: XCTestCase {
    func testInsetFigureUsesInlineLayoutForSubstantivePassage() {
        XCTAssertTrue(
            BriefingFigureLayoutPolicy.usesInlineLayout(
                placement: .inset,
                hasImage: true,
                passageTextLength: 240
            )
        )
    }

    func testFullFigureKeepsStackedLayout() {
        XCTAssertFalse(
            BriefingFigureLayoutPolicy.usesInlineLayout(
                placement: .full,
                hasImage: true,
                passageTextLength: 500
            )
        )
    }

    func testMissingPlacementDefaultsToInset() {
        XCTAssertEqual(BriefingFigureLayoutPolicy.canonicalPlacement(nil), .inset)
    }

    func testMissingAlignmentAlternatesRightThenLeft() {
        XCTAssertEqual(BriefingFigureLayoutPolicy.alignment(nil, fallbackIndex: 0), .right)
        XCTAssertEqual(BriefingFigureLayoutPolicy.alignment(nil, fallbackIndex: 1), .left)
        XCTAssertEqual(BriefingFigureLayoutPolicy.alignment(nil, fallbackIndex: 2), .right)
    }

    func testExplicitAlignmentOverridesAlternatingFallback() {
        XCTAssertEqual(
            BriefingFigureLayoutPolicy.alignment(.left, fallbackIndex: 0),
            .left
        )
    }

    func testTextExclusionMovesBetweenLeftAndRightEdges() throws {
        let textView = DigDeeperTextView(frame: CGRect(x: 0, y: 0, width: 320, height: 200))
        textView.floatingExclusionSize = CGSize(width: 120, height: 120)

        textView.floatingExclusionAlignment = .left
        textView.updateFloatingExclusion(forWidth: 320)
        XCTAssertEqual(try XCTUnwrap(textView.textContainer.exclusionPaths.first).bounds.minX, 0)

        textView.floatingExclusionAlignment = .right
        textView.updateFloatingExclusion(forWidth: 320)
        XCTAssertEqual(try XCTUnwrap(textView.textContainer.exclusionPaths.first).bounds.minX, 200)
    }

    func testCompactInlineFigureIsSmallerThanRegularFigure() {
        let compact = BriefingFigureLayoutPolicy.metrics(for: .compact)
        let regular = BriefingFigureLayoutPolicy.metrics(for: .regular)

        XCTAssertLessThan(compact.imageSize.width, regular.imageSize.width)
        XCTAssertGreaterThan(compact.exclusionSize.width, compact.imageSize.width)
    }
}
