import XCTest
@testable import newsly

final class BriefingReadMarkingTests: XCTestCase {
    func testSegmentMidpointJustBeforeViewportTopHasNotCrossed() {
        let frame = CGRect(x: 0, y: -49.5, width: 100, height: 100)

        XCTAssertFalse(briefingSegmentHasCrossedReadMidpoint(frame: frame))
    }

    func testSegmentMidpointExactlyAtViewportTopHasNotCrossed() {
        let frame = CGRect(x: 0, y: -50, width: 100, height: 100)

        XCTAssertFalse(briefingSegmentHasCrossedReadMidpoint(frame: frame))
    }

    func testSegmentMidpointJustAfterViewportTopHasCrossed() {
        let frame = CGRect(x: 0, y: -50.5, width: 100, height: 100)

        XCTAssertTrue(briefingSegmentHasCrossedReadMidpoint(frame: frame))
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
        let identity = BriefingLensContentIdentity(lensKey: "podcasts", segmentIDs: [1, 2, 3])

        XCTAssertEqual(
            identity,
            BriefingLensContentIdentity(lensKey: "podcasts", segmentIDs: [1, 2, 3])
        )
        XCTAssertNotEqual(
            identity,
            BriefingLensContentIdentity(lensKey: "podcasts", segmentIDs: [2, 3])
        )
        XCTAssertNotEqual(
            identity,
            BriefingLensContentIdentity(lensKey: "articles", segmentIDs: [1, 2, 3])
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
