import SwiftUI

struct BriefingFigureLayoutMetrics: Equatable {
    let imageSize: CGSize
    let exclusionSize: CGSize
}

enum BriefingFigureLayoutPolicy {
    /// The exclusion keeps a horizontal gutter beside the figure but hugs its
    /// bottom edge: any line fragment that touches the exclusion is shortened,
    /// so extra height leaves a narrow line hanging below the image.
    private static let compactMetrics = BriefingFigureLayoutMetrics(
        imageSize: CGSize(width: 116, height: 116),
        exclusionSize: CGSize(width: 128, height: 118)
    )
    private static let regularMetrics = BriefingFigureLayoutMetrics(
        imageSize: CGSize(width: 148, height: 148),
        exclusionSize: CGSize(width: 162, height: 150)
    )

    static func canonicalPlacement(
        _ placement: APIBriefingFigurePlacement?
    ) -> APIBriefingFigurePlacement {
        placement ?? .inset
    }

    static func alignment(
        _ alignment: APIBriefingFigureAlignment?,
        fallbackIndex: Int
    ) -> APIBriefingFigureAlignment {
        alignment ?? (fallbackIndex.isMultiple(of: 2) ? .right : .left)
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
