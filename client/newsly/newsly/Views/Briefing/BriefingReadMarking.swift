import Foundation

let briefingReadCoordinateSpaceName = "briefing.read-tracking"

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

func briefingSegmentHasPassedReadBoundary(
    frame: CGRect,
    readBoundaryY: CGFloat?
) -> Bool {
    guard let readBoundaryY,
          readBoundaryY.isFinite,
          frame.maxY.isFinite else { return false }
    return frame.maxY < readBoundaryY
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

struct BriefingReadBoundaryTracker {
    private var hasObservedBeforeBoundary = false
    private var didMark = false

    mutating func update(hasPassedBoundary: Bool, isEnabled: Bool) -> Bool {
        guard isEnabled else {
            hasObservedBeforeBoundary = false
            return false
        }
        guard !didMark else { return false }

        if !hasPassedBoundary {
            hasObservedBeforeBoundary = true
            return false
        }

        guard hasObservedBeforeBoundary else { return false }
        didMark = true
        return true
    }
}

struct BriefingLensContentIdentity: Hashable {
    let lensKey: String
    let generation: Int
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
