import XCTest
@testable import newsly

@MainActor
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

    func testPinnedReadBoundaryRejectsNonFiniteGeometry() {
        XCTAssertNil(
            briefingPinnedReadBoundaryY(
                expandedChromeHeight: .infinity,
                collapsibleChromeHeight: 160
            )
        )
        XCTAssertNil(
            briefingPinnedReadBoundaryY(
                expandedChromeHeight: 220,
                collapsibleChromeHeight: .nan
            )
        )
    }

    func testSegmentBottomJustBeforePinnedBoundaryIsVisibleAndHasNotPassed() throws {
        let state = try XCTUnwrap(
            briefingSegmentViewportState(
                contentFrame: CGRect(x: 0, y: 100, width: 100, height: 100),
                scrollOffset: 139.5,
                containerHeight: 580,
                readBoundaryY: 60
            )
        )

        XCTAssertTrue(state.isVisible)
        XCTAssertFalse(state.hasPassedReadBoundary)
    }

    func testSegmentBottomExactlyAtPinnedBoundaryHasNotPassed() throws {
        let state = try XCTUnwrap(
            briefingSegmentViewportState(
                contentFrame: CGRect(x: 0, y: 100, width: 100, height: 100),
                scrollOffset: 140,
                containerHeight: 580,
                readBoundaryY: 60
            )
        )

        XCTAssertFalse(state.isVisible)
        XCTAssertFalse(state.hasPassedReadBoundary)
    }

    func testSegmentBottomJustAfterPinnedBoundaryHasPassed() throws {
        let state = try XCTUnwrap(
            briefingSegmentViewportState(
                contentFrame: CGRect(x: 0, y: 100, width: 100, height: 100),
                scrollOffset: 140.5,
                containerHeight: 580,
                readBoundaryY: 60
            )
        )

        XCTAssertFalse(state.isVisible)
        XCTAssertTrue(state.hasPassedReadBoundary)
    }

    func testSegmentBelowViewportIsNeitherVisibleNorPassed() throws {
        let state = try XCTUnwrap(
            briefingSegmentViewportState(
                contentFrame: CGRect(x: 0, y: 800, width: 100, height: 100),
                scrollOffset: -220,
                containerHeight: 580,
                readBoundaryY: 60
            )
        )

        XCTAssertFalse(state.isVisible)
        XCTAssertFalse(state.hasPassedReadBoundary)
    }

    func testSegmentViewportStateRequiresCompleteFiniteGeometry() {
        XCTAssertNil(
            briefingSegmentViewportState(
                contentFrame: CGRect(x: 0, y: 100, width: 100, height: 100),
                scrollOffset: 0,
                containerHeight: 0,
                readBoundaryY: 60
            )
        )
        XCTAssertNil(
            briefingSegmentViewportState(
                contentFrame: CGRect(x: 0, y: -.infinity, width: 100, height: 100),
                scrollOffset: 0,
                containerHeight: 580,
                readBoundaryY: .nan
            )
        )
        XCTAssertNil(
            briefingSegmentViewportState(
                contentFrame: .zero,
                scrollOffset: 0,
                containerHeight: 580,
                readBoundaryY: 60
            )
        )
    }

    func testTrailingClearanceLetsFinalSegmentPassPinnedBoundary() {
        XCTAssertEqual(
            briefingTrailingReadClearance(
                containerHeight: 800,
                topContentInset: 220,
                readBoundaryY: 80
            ),
            941
        )
    }

    func testTrailingClearanceUsesMinimumBeforeGeometryIsAvailable() {
        XCTAssertEqual(
            briefingTrailingReadClearance(
                containerHeight: 0,
                topContentInset: 0,
                readBoundaryY: nil
            ),
            24
        )
    }

    func testTrailingClearanceUsesMinimumForNonFiniteGeometry() {
        XCTAssertEqual(
            briefingTrailingReadClearance(
                containerHeight: .infinity,
                topContentInset: 220,
                readBoundaryY: 80
            ),
            24
        )
        XCTAssertEqual(
            briefingTrailingReadClearance(
                containerHeight: 800,
                topContentInset: 220,
                readBoundaryY: .nan
            ),
            24
        )
        XCTAssertEqual(
            briefingTrailingReadClearance(
                containerHeight: 800,
                topContentInset: .nan,
                readBoundaryY: 80
            ),
            24
        )
    }

    func testVisibleSegmentMarksOnceAfterItsStoredFramePassesBoundary() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )

        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 140.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1]
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 500,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
    }

    func testIncrementalScrollMarksOnlyAfterStrictBoundaryPassage() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )

        for offset in [0.0, 70.0, 120.0, 139.5, 140.0] {
            XCTAssertEqual(
                tracker.updateViewport(
                    scrollOffset: offset,
                    containerHeight: 580,
                    documentGeneration: 1
                ),
                [],
                "Offset \(offset) must not pass the strict boundary"
            )
        }
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 140.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1]
        )
    }

    func testLateLayoutExpansionDelaysReadUntilExpandedBodyPasses() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 300),
                for: 1,
                documentGeneration: 1
            ),
            []
        )

        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 140.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 340,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 340.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1]
        )
    }

    func testScrollBeforeBoundaryMeasurementMarksWhenBoundaryArrives() {
        let tracker = BriefingScrollReadTracker()
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 1,
                    segmentIDs: [1],
                    isEnabled: true,
                    readBoundaryY: nil
                )
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: -220,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 180,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )

        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1])),
            [1]
        )
    }

    func testBoundaryBeforeLateFrameStillRecoversPriorTraversal() {
        let tracker = BriefingScrollReadTracker()
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 1,
                    segmentIDs: [1],
                    isEnabled: true,
                    readBoundaryY: nil
                )
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: -220,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 180,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1])),
            []
        )

        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            [1]
        )
    }

    func testBoundaryBeforeUsableViewportStillRecoversPriorTraversal() {
        let tracker = BriefingScrollReadTracker()
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 1,
                    segmentIDs: [1],
                    isEnabled: true,
                    readBoundaryY: nil
                )
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: -220,
                containerHeight: 0,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 180,
                containerHeight: 0,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1])),
            []
        )

        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 180,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1]
        )
    }

    func testLateBoundaryWithoutTraversalDoesNotMarkInitiallyPastSegment() {
        let tracker = BriefingScrollReadTracker()
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 1,
                    segmentIDs: [1],
                    isEnabled: true,
                    readBoundaryY: nil
                )
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 180,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )

        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1])),
            []
        )
    }

    func testDisablingBeforeBoundaryMeasurementClearsPriorTraversal() {
        let tracker = BriefingScrollReadTracker()
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 1,
                    segmentIDs: [1],
                    isEnabled: true,
                    readBoundaryY: nil
                )
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: -220,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 180,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 1,
                    segmentIDs: [1],
                    isEnabled: false,
                    readBoundaryY: nil
                )
            ),
            []
        )

        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1])),
            []
        )
    }

    func testInitialGeometryAlreadyPastBoundaryNeverMarksRead() {
        let tracker = configuredReadTracker(segmentIDs: [1], scrollOffset: 180)

        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 300,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
    }

    func testFastScrollSweepMarksSegmentThatCrossedTheViewportBetweenSamples() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        let frame = CGRect(x: 0, y: 800, width: 100, height: 100)
        XCTAssertEqual(
            tracker.updateFrame(frame, for: 1, documentGeneration: 1),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 840.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1]
        )
    }

    func testOffscreenGeometryAloneDoesNotMakeSegmentEligible() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 400, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: -400, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
    }

    func testZeroHeightPlaceholderCannotCreateReadEligibility() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(.zero, for: 1, documentGeneration: 1),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 500,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
    }

    func testOneFastScrollMarksAllVisibleSegmentsInDocumentOrder() {
        let tracker = configuredReadTracker(segmentIDs: [1, 2])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 230, width: 100, height: 100),
                for: 2,
                documentGeneration: 1
            ),
            []
        )

        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 500,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1, 2]
        )
    }

    func testDisablingTrackingClearsVisibilityEligibility() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(
                makeReadConfiguration(segmentIDs: [1], isEnabled: false)
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 140.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1])),
            []
        )
    }

    func testReenabledVisibleSegmentCanMarkOnLaterPassage() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(
                makeReadConfiguration(segmentIDs: [1], isEnabled: false)
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 0,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1])),
            []
        )

        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 140.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1]
        )
    }

    func testNewDocumentGenerationDoesNotReusePriorVisibility() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 2,
                    segmentIDs: [1],
                    isEnabled: true,
                    readBoundaryY: 60
                )
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 180,
                containerHeight: 580,
                documentGeneration: 2
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 2
            ),
            []
        )
    }

    func testLateCallbacksFromOlderDocumentCannotResetTracker() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateConfiguration(
                BriefingReadTrackingConfiguration(
                    documentGeneration: 2,
                    segmentIDs: [2],
                    isEnabled: true,
                    readBoundaryY: 60
                )
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: -220,
                containerHeight: 580,
                documentGeneration: 2
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 500,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 2,
                documentGeneration: 2
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 140.5,
                containerHeight: 580,
                documentGeneration: 2
            ),
            [2]
        )
    }

    func testAppendingSegmentPreservesExistingReadEligibility() {
        let tracker = configuredReadTracker(segmentIDs: [1])
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 100, width: 100, height: 100),
                for: 1,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: [1, 2])),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 140.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [1]
        )
        XCTAssertEqual(
            tracker.updateFrame(
                CGRect(x: 0, y: 500, width: 100, height: 100),
                for: 2,
                documentGeneration: 1
            ),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: 540.5,
                containerHeight: 580,
                documentGeneration: 1
            ),
            [2]
        )
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

    func testLensPageEqualityTracksItsOwnScrollEligibility() {
        let chromeCollapse = BriefingChromeCollapseModel()
        let ineligiblePage = makeLensPage(
            shouldScrollToTop: false,
            chromeCollapse: chromeCollapse
        )

        XCTAssertEqual(
            ineligiblePage,
            makeLensPage(shouldScrollToTop: false, chromeCollapse: chromeCollapse)
        )
        XCTAssertNotEqual(
            ineligiblePage,
            makeLensPage(shouldScrollToTop: true, chromeCollapse: chromeCollapse)
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

    private func configuredReadTracker(
        segmentIDs: [Int],
        scrollOffset: CGFloat = -220
    ) -> BriefingScrollReadTracker {
        let tracker = BriefingScrollReadTracker()
        XCTAssertEqual(
            tracker.updateConfiguration(makeReadConfiguration(segmentIDs: segmentIDs)),
            []
        )
        XCTAssertEqual(
            tracker.updateViewport(
                scrollOffset: scrollOffset,
                containerHeight: 580,
                documentGeneration: 1
            ),
            []
        )
        return tracker
    }

    private func makeReadConfiguration(
        segmentIDs: [Int],
        isEnabled: Bool = true
    ) -> BriefingReadTrackingConfiguration {
        BriefingReadTrackingConfiguration(
            documentGeneration: 1,
            segmentIDs: segmentIDs,
            isEnabled: isEnabled,
            readBoundaryY: 60
        )
    }

    private func makeLensPage(
        shouldScrollToTop: Bool,
        chromeCollapse: BriefingChromeCollapseModel
    ) -> BriefingLensPageView {
        BriefingLensPageView(
            lensKey: "articles",
            lensTitle: "Articles",
            renderModel: nil,
            isReadTrackingEnabled: false,
            readBoundaryY: nil,
            documentGeneration: 1,
            scrollToTopRequest: 2,
            shouldScrollToTop: shouldScrollToTop,
            error: nil,
            continuationError: nil,
            isLoadingContinuation: false,
            chromeCollapse: chromeCollapse,
            collapsibleChromeHeight: 120,
            topContentInset: 180,
            onOpenSource: { _ in },
            onOpenDiscussion: { _ in },
            onDig: { _, _ in },
            onRefresh: {},
            onLoad: {},
            onRetry: {},
            onFirstPassageVisible: {},
            onScrolledDown: {},
            onMarkSegmentSeen: { _ in },
            onSetHeaderPinned: { _ in }
        )
    }
}
