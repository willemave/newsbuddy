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
        let lineStep = BriefingPassageView.lineStep(for: .large)
        let compact = BriefingFigureLayoutPolicy.metrics(for: .compact, lineStep: lineStep)
        let regular = BriefingFigureLayoutPolicy.metrics(for: .regular, lineStep: lineStep)

        XCTAssertLessThan(compact.imageSize.width, regular.imageSize.width)
        XCTAssertGreaterThan(compact.exclusionSize.width, compact.imageSize.width)
    }

    func testFigureSpansWholePassageLines() {
        for size: DynamicTypeSize in [.small, .large, .xxxLarge] {
            let lineStep = BriefingPassageView.lineStep(for: size)
            let metrics = BriefingFigureLayoutPolicy.metrics(for: .compact, lineStep: lineStep)
            let lines = (metrics.exclusionSize.height + BriefingAttributedTextBuilder.passageLineSpacing) / lineStep
            XCTAssertEqual(lines, lines.rounded(), accuracy: 0.001)
            XCTAssertEqual(metrics.imageSize.height, metrics.exclusionSize.height)
        }
    }

    func testTextReturnsToFullWidthOnTheLineAfterTheFigure() throws {
        let traits = UITraitCollection(preferredContentSizeCategory: .extraExtraLarge)
        let paragraph = APIBriefingParagraph(runs: [
            APIBriefingRun(
                kind: .text,
                text: String(repeating: "Research agents screened disease targets in greater depth. ", count: 10),
                sourceKey: nil,
                insightId: nil
            )
        ])
        let content = BriefingAttributedTextBuilder().build(paragraphs: [paragraph], weight: nil)
        let metrics = BriefingFigureLayoutPolicy.metrics(
            for: .compact,
            lineStep: BriefingPassageView.lineStep(for: .xxLarge)
        )

        for alignment: APIBriefingFigureAlignment in [.left, .right] {
            let textView = DigDeeperTextView(frame: CGRect(x: 0, y: 0, width: 370, height: 800))
            textView.textContainerInset = .zero
            textView.textContainer.lineFragmentPadding = 0
            textView.attributedText = BriefingPassageView.scaledAttributedText(
                content.attributedText,
                compatibleWith: traits
            )
            textView.floatingExclusionSize = metrics.exclusionSize
            textView.floatingExclusionAlignment = alignment
            textView.layoutIfNeeded()

            let layoutManager = try XCTUnwrap(textView.textLayoutManager)
            var lineFrames: [CGRect] = []
            layoutManager.enumerateTextLayoutFragments(
                from: layoutManager.documentRange.location,
                options: [.ensuresLayout]
            ) { fragment in
                lineFrames += fragment.textLineFragments.map {
                    $0.typographicBounds.offsetBy(
                        dx: fragment.layoutFragmentFrame.minX,
                        dy: fragment.layoutFragmentFrame.minY
                    )
                }
                return true
            }

            let beside = lineFrames.filter { $0.minY < metrics.exclusionSize.height }
            let below = try XCTUnwrap(lineFrames.first { $0.minY >= metrics.exclusionSize.height })
            // No indented line may hang below the image.
            XCTAssertLessThanOrEqual(try XCTUnwrap(beside.last).maxY, metrics.imageSize.height + 0.5)
            for line in beside {
                if alignment == .left {
                    XCTAssertEqual(line.minX, metrics.exclusionSize.width, accuracy: 0.5)
                } else {
                    XCTAssertLessThanOrEqual(line.maxX, 370 - metrics.exclusionSize.width + 0.5)
                }
            }
            XCTAssertEqual(below.minX, 0, accuracy: 0.5)
            XCTAssertGreaterThan(below.width, 370 - metrics.exclusionSize.width)
        }
    }
}
