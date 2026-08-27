import Foundation

func briefingPinnedReadBoundaryY(
    expandedChromeHeight: CGFloat,
    collapsibleChromeHeight: CGFloat
) -> CGFloat? {
    guard expandedChromeHeight.isFinite,
          collapsibleChromeHeight.isFinite,
          expandedChromeHeight > 0,
          collapsibleChromeHeight >= 0 else { return nil }
    return max(expandedChromeHeight - collapsibleChromeHeight, 0)
}

struct BriefingSegmentViewportState: Equatable {
    let isVisible: Bool
    let hasPassedReadBoundary: Bool
}

func briefingSegmentViewportState(
    contentFrame: CGRect,
    scrollOffset: CGFloat,
    containerHeight: CGFloat,
    readBoundaryY: CGFloat?
) -> BriefingSegmentViewportState? {
    guard let readBoundaryY,
          readBoundaryY.isFinite,
          contentFrame.minY.isFinite,
          contentFrame.maxY.isFinite,
          contentFrame.height.isFinite,
          contentFrame.height > 0,
          scrollOffset.isFinite,
          containerHeight.isFinite,
          containerHeight > 0 else { return nil }

    let screenMinY = contentFrame.minY - scrollOffset
    let screenMaxY = contentFrame.maxY - scrollOffset
    let viewportMaxY = containerHeight
    return BriefingSegmentViewportState(
        isVisible: screenMaxY > readBoundaryY && screenMinY < viewportMaxY,
        hasPassedReadBoundary: screenMaxY < readBoundaryY
    )
}

/// `ScrollGeometry.containerSize` excludes the top content inset that places
/// the pager beneath expanded chrome. Add that stable inset back so the final
/// segment can cross the boundary without measuring the scroll view's frame.
func briefingTrailingReadClearance(
    containerHeight: CGFloat,
    topContentInset: CGFloat,
    readBoundaryY: CGFloat?
) -> CGFloat {
    let minimumClearance: CGFloat = 24
    guard containerHeight.isFinite,
          containerHeight > 0,
          topContentInset.isFinite,
          topContentInset >= 0,
          let readBoundaryY,
          readBoundaryY.isFinite else { return minimumClearance }
    return max(
        containerHeight + topContentInset - readBoundaryY + 1,
        minimumClearance
    )
}

struct BriefingLensContentIdentity: Hashable, Sendable {
    let lensKey: String
    let generation: Int
}

struct BriefingReadTrackingConfiguration: Equatable {
    let documentGeneration: Int
    let segmentIDs: [Int]
    let isEnabled: Bool
    let readBoundaryY: CGFloat?
}

/// Owns read passage at the scroll-view level. Segment frames are measured in
/// the content's stable coordinate space, so a lazy row can be recycled before
/// its full body crosses the pinned boundary without losing the crossing.
@MainActor
final class BriefingScrollReadTracker {
    private var documentGeneration: Int?
    private var segmentIDs: [Int] = []
    private var framesBySegmentID: [Int: CGRect] = [:]
    private var visibleSegmentIDs: Set<Int> = []
    private var markedSegmentIDs: Set<Int> = []
    private var isEnabled = false
    private var scrollOffset: CGFloat = 0
    private var containerHeight: CGFloat = 0
    private var hasViewportSample = false
    private var readBoundaryY: CGFloat?
    /// Keeps passage recoverable when viewport, boundary, and lazy-row frame
    /// callbacks arrive in any order. Reset when this page stops tracking.
    private var minimumObservedScrollOffset: CGFloat?

    func updateConfiguration(
        _ configuration: BriefingReadTrackingConfiguration
    ) -> [Int] {
        guard prepareDocument(generation: configuration.documentGeneration) else {
            return []
        }
        segmentIDs = configuration.segmentIDs
        isEnabled = configuration.isEnabled
        readBoundaryY = configuration.readBoundaryY
        pruneRemovedSegments()
        if !isEnabled {
            visibleSegmentIDs.removeAll()
            minimumObservedScrollOffset = nil
        } else if hasViewportSample {
            rememberMinimumScrollOffset(scrollOffset)
        }
        return evaluate()
    }

    func updateFrame(
        _ frame: CGRect,
        for segmentID: Int,
        documentGeneration: Int
    ) -> [Int] {
        guard prepareDocument(generation: documentGeneration) else { return [] }
        guard frame.minY.isFinite,
              frame.maxY.isFinite,
              frame.height.isFinite,
              frame.height > 0 else {
            framesBySegmentID.removeValue(forKey: segmentID)
            visibleSegmentIDs.remove(segmentID)
            return []
        }
        framesBySegmentID[segmentID] = frame
        return evaluate()
    }

    func updateViewport(
        scrollOffset: CGFloat,
        containerHeight: CGFloat,
        documentGeneration: Int
    ) -> [Int] {
        guard prepareDocument(generation: documentGeneration) else { return [] }
        self.scrollOffset = scrollOffset
        self.containerHeight = containerHeight
        hasViewportSample = true
        if isEnabled {
            rememberMinimumScrollOffset(scrollOffset)
        }
        return evaluate()
    }

    private func prepareDocument(generation: Int) -> Bool {
        if let documentGeneration {
            guard generation >= documentGeneration else { return false }
            guard generation != documentGeneration else { return true }
        }
        documentGeneration = generation
        segmentIDs = []
        framesBySegmentID = [:]
        visibleSegmentIDs = []
        markedSegmentIDs = []
        scrollOffset = 0
        containerHeight = 0
        hasViewportSample = false
        minimumObservedScrollOffset = nil
        return true
    }

    private func rememberMinimumScrollOffset(_ offset: CGFloat) {
        guard offset.isFinite else { return }
        minimumObservedScrollOffset = min(minimumObservedScrollOffset ?? offset, offset)
    }

    private func pruneRemovedSegments() {
        let validIDs = Set(segmentIDs)
        framesBySegmentID = framesBySegmentID.filter { validIDs.contains($0.key) }
        visibleSegmentIDs.formIntersection(validIDs)
        markedSegmentIDs.formIntersection(validIDs)
    }

    private func evaluate() -> [Int] {
        guard isEnabled else { return [] }

        var newlyRead: [Int] = []
        for segmentID in segmentIDs {
            guard !markedSegmentIDs.contains(segmentID),
                  let frame = framesBySegmentID[segmentID],
                  let state = briefingSegmentViewportState(
                      contentFrame: frame,
                      scrollOffset: scrollOffset,
                      containerHeight: containerHeight,
                      readBoundaryY: readBoundaryY
                  ) else { continue }

            let sweptThroughViewport = minimumObservedScrollOffset.map {
                scrollSweepIncludesSegment(
                    frame,
                    from: $0,
                    to: scrollOffset
                )
            } ?? false
            if state.isVisible || sweptThroughViewport {
                visibleSegmentIDs.insert(segmentID)
            }
            guard state.hasPassedReadBoundary,
                  visibleSegmentIDs.remove(segmentID) != nil else { continue }
            markedSegmentIDs.insert(segmentID)
            newlyRead.append(segmentID)
        }
        return newlyRead
    }

    private func scrollSweepIncludesSegment(
        _ frame: CGRect,
        from previousOffset: CGFloat,
        to currentOffset: CGFloat
    ) -> Bool {
        guard currentOffset > previousOffset,
              let readBoundaryY,
              readBoundaryY.isFinite,
              containerHeight.isFinite,
              containerHeight > 0 else { return false }
        let entryOffset = frame.minY - containerHeight
        let passageOffset = frame.maxY - readBoundaryY
        return currentOffset > entryOffset && previousOffset < passageOffset
    }
}

enum BriefingLensDocumentGenerationPolicy {
    static func preservesGeneration(previousIDs: [Int], mergedIDs: [Int]) -> Bool {
        mergedIDs == previousIDs || mergedIDs.starts(with: previousIDs)
    }
}

func uniqueBriefingSourceKeys(_ sourceKeys: [String]) -> [String] {
    var seen = Set<String>()
    var result: [String] = []
    for sourceKey in sourceKeys where seen.insert(sourceKey).inserted {
        result.append(sourceKey)
    }
    return result
}

extension APIBriefingBlock {
    var briefingDirectSourceKeys: [String] {
        uniqueBriefingSourceKeys([sourceKey].compactMap { $0 })
    }

    var briefingSourceLinkKeys: [String] {
        uniqueBriefingSourceKeys(
            (paragraphs ?? []).flatMap { paragraph in
                paragraph.runs.flatMap { run -> [String] in
                    if run.kind == .source_link, let sourceKey = run.sourceKey {
                        return [sourceKey]
                    }
                    if run.kind == .text {
                        return BriefingAttributedTextBuilder.sourceKeys(in: run.text)
                    }
                    return []
                }
            }
        )
    }

    var briefingFallbackReadSourceKeys: [String] {
        uniqueBriefingSourceKeys(briefingDirectSourceKeys + briefingSourceLinkKeys)
    }
}
