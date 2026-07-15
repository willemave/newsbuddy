import XCTest
@testable import newsly

final class BriefingReadMarkingTests: XCTestCase {
    func testPinnedReadBoundaryUsesRemainingChromeHeight() {
        XCTAssertEqual(
            briefingPinnedReadBoundaryY(
                expandedChromeHeight: 220,
                collapsibleChromeHeight: 160
            ),
            60
        )
    }

    func testPinnedReadBoundaryIsUnavailableBeforeChromeMeasurement() {
        XCTAssertNil(
            briefingPinnedReadBoundaryY(
                expandedChromeHeight: 0,
                collapsibleChromeHeight: 0
            )
        )
    }

    func testSegmentMidpointJustBeforePinnedBoundaryHasNotCrossed() {
        let frame = CGRect(x: 0, y: 30.5, width: 100, height: 100)

        XCTAssertFalse(
            briefingSegmentHasCrossedReadMidpoint(frame: frame, readBoundaryY: 80)
        )
    }

    func testSegmentMidpointExactlyAtPinnedBoundaryHasNotCrossed() {
        let frame = CGRect(x: 0, y: 30, width: 100, height: 100)

        XCTAssertFalse(
            briefingSegmentHasCrossedReadMidpoint(frame: frame, readBoundaryY: 80)
        )
    }

    func testSegmentMidpointJustAfterPinnedBoundaryHasCrossed() {
        let frame = CGRect(x: 0, y: 29.5, width: 100, height: 100)

        XCTAssertTrue(
            briefingSegmentHasCrossedReadMidpoint(frame: frame, readBoundaryY: 80)
        )
    }

    func testSegmentMidpointDoesNotCrossBeforeBoundaryMeasurement() {
        let frame = CGRect(x: 0, y: -100, width: 100, height: 100)

        XCTAssertFalse(
            briefingSegmentHasCrossedReadMidpoint(frame: frame, readBoundaryY: nil)
        )
    }

    func testInitialObservationAfterMidpointDoesNotMarkRead() {
        var tracker = BriefingMidpointReadTracker()

        XCTAssertFalse(tracker.update(hasCrossedMidpoint: true, isEnabled: true))
    }

    func testBeforeToAfterMidpointMarksReadExactlyOnce() {
        var tracker = BriefingMidpointReadTracker()

        XCTAssertFalse(tracker.update(hasCrossedMidpoint: false, isEnabled: true))
        XCTAssertTrue(tracker.update(hasCrossedMidpoint: true, isEnabled: true))
        XCTAssertFalse(tracker.update(hasCrossedMidpoint: true, isEnabled: true))
    }

    func testDisablingTrackingClearsPriorMidpointObservation() {
        var tracker = BriefingMidpointReadTracker()

        XCTAssertFalse(tracker.update(hasCrossedMidpoint: false, isEnabled: true))
        XCTAssertFalse(tracker.update(hasCrossedMidpoint: false, isEnabled: false))
        XCTAssertFalse(tracker.update(hasCrossedMidpoint: true, isEnabled: true))
    }

    func testLensContentIdentityChangesOnlyWithLensOrOrderedSegments() {
        let identity = BriefingLensContentIdentity(lensKey: "podcasts", generation: 2)

        XCTAssertEqual(
            identity,
            BriefingLensContentIdentity(lensKey: "podcasts", generation: 2)
        )
        XCTAssertNotEqual(
            identity,
            BriefingLensContentIdentity(lensKey: "podcasts", generation: 3)
        )
        XCTAssertNotEqual(
            identity,
            BriefingLensContentIdentity(lensKey: "articles", generation: 2)
        )
    }

    func testDocumentGenerationPolicyPreservesOnlyEqualOrAppendedIDs() {
        XCTAssertTrue(
            BriefingLensDocumentGenerationPolicy.preservesGeneration(
                previousIDs: [3, 2],
                mergedIDs: [3, 2]
            )
        )
        XCTAssertTrue(
            BriefingLensDocumentGenerationPolicy.preservesGeneration(
                previousIDs: [3, 2],
                mergedIDs: [3, 2, 1]
            )
        )
        XCTAssertFalse(
            BriefingLensDocumentGenerationPolicy.preservesGeneration(
                previousIDs: [3, 2],
                mergedIDs: [4, 3, 2]
            )
        )
        XCTAssertFalse(
            BriefingLensDocumentGenerationPolicy.preservesGeneration(
                previousIDs: [3, 2],
                mergedIDs: [3]
            )
        )
        XCTAssertFalse(
            BriefingLensDocumentGenerationPolicy.preservesGeneration(
                previousIDs: [3, 2],
                mergedIDs: [2, 3]
            )
        )
    }

    func testBlockSourceLinkKeysPreserveOrderAndRemoveDuplicates() {
        let block = APIBriefingBlock(
            type: .passage,
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .source_link, text: "First", sourceKey: "content:1"),
                        APIBriefingRun(kind: .text, text: " body "),
                        APIBriefingRun(kind: .source_link, text: "Second", sourceKey: "news:2"),
                    ]
                ),
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .source_link, text: "First again", sourceKey: "content:1")
                    ]
                )
            ]
        )

        XCTAssertEqual(block.briefingSourceLinkKeys, ["content:1", "news:2"])
    }

    func testFallbackReadSourceKeysIncludeDirectAndInlineSourceLinks() {
        let block = APIBriefingBlock(
            type: .passage,
            paragraphs: [
                APIBriefingParagraph(
                    runs: [
                        APIBriefingRun(kind: .source_link, text: "Linked", sourceKey: "news:2")
                    ]
                )
            ],
            sourceKey: "content:1"
        )

        XCTAssertEqual(block.briefingFallbackReadSourceKeys, ["content:1", "news:2"])
    }
}
