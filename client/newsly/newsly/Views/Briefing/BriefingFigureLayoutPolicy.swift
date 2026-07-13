import SwiftUI

struct BriefingFigureLayoutMetrics: Equatable {
    let imageSize: CGSize
    let exclusionSize: CGSize
}

enum BriefingFigureLayoutPolicy {
    private static let compactMetrics = BriefingFigureLayoutMetrics(
        imageSize: CGSize(width: 116, height: 116),
        exclusionSize: CGSize(width: 128, height: 128)
    )
    private static let regularMetrics = BriefingFigureLayoutMetrics(
        imageSize: CGSize(width: 148, height: 148),
        exclusionSize: CGSize(width: 162, height: 160)
    )

    static func canonicalPlacement(
        _ placement: APIBriefingFigurePlacement?
    ) -> APIBriefingFigurePlacement {
        placement ?? .inset
    }

    static func usesInlineLayout(
        placement: APIBriefingFigurePlacement?,
        hasImage: Bool,
        passageTextLength: Int
    ) -> Bool {
        canonicalPlacement(placement) == .inset
            && hasImage
            && passageTextLength >= 240
    }

    static func metrics(
        for horizontalSizeClass: UserInterfaceSizeClass?
    ) -> BriefingFigureLayoutMetrics {
        horizontalSizeClass == .compact ? compactMetrics : regularMetrics
    }
}
