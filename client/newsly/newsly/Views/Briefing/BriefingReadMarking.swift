import Foundation

let briefingReadCoordinateSpaceName = "briefing.read-tracking"

func briefingPinnedReadBoundaryY(
    expandedChromeHeight: CGFloat,
    collapsibleChromeHeight: CGFloat
) -> CGFloat? {
    guard expandedChromeHeight > 0 else { return nil }
    return max(expandedChromeHeight - collapsibleChromeHeight, 0)
}

func briefingSegmentHasCrossedReadMidpoint(
    frame: CGRect,
    readBoundaryY: CGFloat?
) -> Bool {
    guard let readBoundaryY else { return false }
    return frame.midY < readBoundaryY
}

struct BriefingMidpointReadTracker {
    private var hasObservedBeforeMidpoint = false
    private var didMark = false

    mutating func update(hasCrossedMidpoint: Bool, isEnabled: Bool) -> Bool {
        guard isEnabled else {
            hasObservedBeforeMidpoint = false
            return false
        }
        guard !didMark else { return false }

        if !hasCrossedMidpoint {
            hasObservedBeforeMidpoint = true
            return false
        }

        guard hasObservedBeforeMidpoint else { return false }
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
