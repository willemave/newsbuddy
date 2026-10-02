import SwiftUI

struct BriefingFigureLayoutMetrics: Equatable {
    let imageSize: CGSize
    let exclusionSize: CGSize
}

enum BriefingFigureLayoutPolicy {
    private static let compactSide: CGFloat = 116
    private static let regularSide: CGFloat = 148
    /// Horizontal gutter between the figure and the wrapped text.
    private static let gutter: CGFloat = 12

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

    /// The figure spans a whole number of passage lines. Any line fragment
    /// that touches the exclusion is shortened, so an exclusion ending
    /// mid-line leaves an indented line hanging below the image. Snapping to
    /// `lineStep` (line height plus line spacing) makes the exclusion end
    /// exactly where the next full-width line begins.
    static func metrics(
        for horizontalSizeClass: UserInterfaceSizeClass?,
        lineStep: CGFloat
    ) -> BriefingFigureLayoutMetrics {
        let side = horizontalSizeClass == .compact ? compactSide : regularSide
        let lineCount = max(1, (side / lineStep).rounded())
        let height = lineCount * lineStep - BriefingAttributedTextBuilder.passageLineSpacing
        return BriefingFigureLayoutMetrics(
            imageSize: CGSize(width: side, height: height),
            exclusionSize: CGSize(width: side + gutter, height: height)
        )
    }
}
