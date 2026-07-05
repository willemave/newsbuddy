import CoreGraphics
import Foundation

struct BriefingSourceLinkPosition: Equatable {
    let sourceKey: String
    let maxY: CGFloat
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
                paragraph.runs.compactMap { run in
                    guard run.kind == .source_link else { return nil }
                    return run.sourceKey
                }
            }
        )
    }

    var briefingFallbackReadSourceKeys: [String] {
        uniqueBriefingSourceKeys(briefingDirectSourceKeys + briefingSourceLinkKeys)
    }
}
