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

    func testCompactInlineFigureIsSmallerThanRegularFigure() {
        let compact = BriefingFigureLayoutPolicy.metrics(for: .compact)
        let regular = BriefingFigureLayoutPolicy.metrics(for: .regular)

        XCTAssertLessThan(compact.imageSize.width, regular.imageSize.width)
        XCTAssertGreaterThan(compact.exclusionSize.width, compact.imageSize.width)
    }
}
