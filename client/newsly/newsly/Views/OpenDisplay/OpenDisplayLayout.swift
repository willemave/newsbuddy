//
//  OpenDisplayLayout.swift
//  newsly
//

import SwiftUI

/// Which root shell the app shows.
enum RootLayout: Equatable {
    /// Every iPhone and the closed iPhone Duo: tabs, floating bar, pushes.
    case compact
    /// The open iPhone Duo: rail and the selected tier.
    case openDisplay

    /// Regular in both directions is the open inner display. A Pro Max in
    /// landscape (regular × compact), iPhone windows on iPad or Mac, and a
    /// compact-width split all keep the compact layout. Never orientation or
    /// idiom.
    static func resolve(
        horizontal: UserInterfaceSizeClass?,
        vertical: UserInterfaceSizeClass?
    ) -> RootLayout {
        horizontal == .regular && vertical == .regular ? .openDisplay : .compact
    }
}

enum OpenDisplayMetrics {
    /// Tight, icon-only rail.
    static let railWidth: CGFloat = 52
    /// Podcasts, Articles, and Knowledge lists: a narrow sidebar, so the
    /// item beside it gets most of the display.
    static let sidebarWidth: CGFloat = 300
    /// News reflowed beside a story ends this far before the hinge line.
    static let newsHingeClearance: CGFloat = 40
    /// One-column News reads at this width, centred.
    static let singleColumnReadingWidth: CGFloat = 640

    /// The fold when it runs top to bottom (the wide hold). Nil for a
    /// horizontal fold (upright hold) or no fold at all.
    static func verticalFold(_ foldFrame: CGRect?, contentHeight: CGFloat) -> CGRect? {
        guard let foldFrame, foldFrame.height >= contentHeight * 0.5 else { return nil }
        return foldFrame
    }

    /// The hinge line in content coordinates: the centre of a vertical fold.
    static func hingeX(foldFrame: CGRect?, contentHeight: CGFloat) -> CGFloat? {
        verticalFold(foldFrame, contentHeight: contentHeight)?.midX
    }

    /// News beside a story or chat: ends 40 pt before the hinge, or uses a
    /// readable share of the width when there is no vertical hinge.
    static func newsLeadingWidth(hingeX: CGFloat?, contentWidth: CGFloat) -> CGFloat {
        if let hingeX { return max(hingeX - newsHingeClearance, sidebarWidth) }
        return min(max(contentWidth * 0.42, sidebarWidth), 420)
    }
}

/// How Briefing lays out its lenses.
enum BriefingLayout: Equatable {
    /// Phones and the closed Duo: masthead, pills, and a horizontal lens pager.
    case pager
    /// The open display: one lens in a column, picked by the rail.
    case column(BriefingColumnLayout)
}

/// One lens on the open display. News (and Start Here) reads as two columns
/// split at the fold, one centred column, or one column filling the leading
/// page beside an open story; Podcasts and Articles are Knowledge-style rows
/// in the sidebar.
struct BriefingColumnLayout: Equatable {
    var columnSplit: BriefingColumnSplit?
    /// One-column News with nothing open reads at this width, centred.
    var singleColumnMaxWidth: CGFloat?
    var presentsCardsAsRows = false
    /// The source open beside the column.
    var selectedSourceKey: String?
}

/// How the open display arranges a Briefing tier.
enum OpenBriefingArrangement: Equatable {
    /// News with nothing open: the full spread, in two columns split at the
    /// fold, or one centred column.
    case spread(BriefingColumnSplit?)
    /// News reflowed to one leading column beside a story or a chat.
    case newsBeside(OpenTrailingPane)
    /// A chat on the leading page beside the open story.
    case chatBesideStory
    /// Podcasts and Articles: the sidebar list beside the trailing pane.
    case listBeside(OpenTrailingPane)

    static func resolve(
        isNewsLike: Bool,
        hasStory: Bool,
        hasChat: Bool,
        preference: NewsColumnPreference,
        split: BriefingColumnSplit?
    ) -> OpenBriefingArrangement {
        guard isNewsLike else {
            return .listBeside(hasChat ? .chat : (hasStory ? .detail : .interstitial))
        }
        switch (hasStory, hasChat) {
        case (true, true): return .chatBesideStory
        case (true, false): return .newsBeside(.detail)
        case (false, true): return .newsBeside(.chat)
        case (false, false): return .spread(preference == .double ? split : nil)
        }
    }
}

/// What fills the trailing page beside a list or reflowed News.
enum OpenTrailingPane: Equatable {
    case detail
    case chat
    case interstitial
}

/// Destinations on the open-display rail. Knowledge is one list, as on the
/// phone: saved items, chats, and decks together.
enum OpenRailDestination: Hashable {
    case startHere
    case news
    case lens(String)
    case knowledge

    /// The rail item that reflects the current root and Briefing selection.
    static func current(
        selectedTab: RootTab,
        isStartHereSelected: Bool,
        isNewsTierSelected: Bool,
        selectedLensKey: String?
    ) -> OpenRailDestination? {
        switch selectedTab {
        case .knowledge:
            return .knowledge
        case .briefing:
            if isStartHereSelected { return .startHere }
            if isNewsTierSelected { return .news }
            return selectedLensKey.map(OpenRailDestination.lens)
        }
    }
}

/// Re-targets a pushed Briefing detail at the item the reader paged to, so
/// the selection carried across a layout change is the one on screen.
func retargetedBriefingSelection(
    _ route: ContentDetailRoute,
    visibleContentId: Int?
) -> ContentDetailRoute {
    guard let visibleContentId, visibleContentId != route.contentId,
          route.allContentIds.contains(visibleContentId) else { return route }
    return ContentDetailRoute(
        contentId: visibleContentId,
        contentType: route.contentType,
        allContentIds: route.allContentIds,
        navigationSurface: route.navigationSurface
    )
}

private struct DetailPaneRootKey: EnvironmentKey {
    static let defaultValue = false
}

private struct DetailVisibleContentChangeKey: EnvironmentKey {
    static let defaultValue: (@MainActor (Int) -> Void)? = nil
}

private struct SupportsOpenDisplayKey: EnvironmentKey {
    static let defaultValue = false
}

private struct InOpenDisplayPaneKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// True when a detail is the root of the open-display detail pane: it has
    /// no back button and list selection replaces paging.
    var isDetailPaneRoot: Bool {
        get { self[DetailPaneRootKey.self] }
        set { self[DetailPaneRootKey.self] = newValue }
    }

    /// True on a device that can open (it is open now, or a hinge has been
    /// seen). Settings shows open-display preferences only then.
    var supportsOpenDisplay: Bool {
        get { self[SupportsOpenDisplayKey.self] }
        set { self[SupportsOpenDisplayKey.self] = newValue }
    }

    /// True for screens shown in an open-display pane. They run to the
    /// trailing screen edge under the floating status bar and camera; a
    /// navigation stack's UIKit host would otherwise re-reserve that column.
    var isInOpenDisplayPane: Bool {
        get { self[InOpenDisplayPaneKey.self] }
        set { self[InOpenDisplayPaneKey.self] = newValue }
    }

    /// Reports the item a pushed detail is showing after the reader pages.
    var onDetailVisibleContentChange: (@MainActor (Int) -> Void)? {
        get { self[DetailVisibleContentChangeKey.self] }
        set { self[DetailVisibleContentChangeKey.self] = newValue }
    }
}

/// Two columns that meet at the fold: the gutter between them is the fold
/// band, centred on the hinge; text runs in both columns.
struct BriefingColumnSplit: Equatable {
    let leadingWidth: CGFloat
    let gap: CGFloat

    static let minimumSpreadWidth: CGFloat = 760
    static let fallbackGap: CGFloat = 32

    /// Splits at a vertical fold when the system reports one; otherwise into
    /// equal halves once the area is wide enough for two columns. A
    /// horizontal fold (the upright hold) or a narrow area keeps one column.
    static func resolve(width: CGFloat, height: CGFloat, foldFrame: CGRect?) -> BriefingColumnSplit? {
        if let foldFrame = OpenDisplayMetrics.verticalFold(foldFrame, contentHeight: height),
           foldFrame.minX > width * 0.25, foldFrame.maxX < width * 0.75 {
            return BriefingColumnSplit(leadingWidth: foldFrame.minX, gap: foldFrame.width)
        }
        guard width >= minimumSpreadWidth else { return nil }
        return BriefingColumnSplit(
            leadingWidth: ((width - fallbackGap) / 2).rounded(),
            gap: fallbackGap
        )
    }
}

/// Distributes segments into two columns, each next segment going to the
/// shorter column by estimated height, so the columns stay balanced as the
/// lens pages in. Reading order is preserved within each column.
func briefingSpreadColumns(
    _ segments: [BriefingSegmentRenderModel]
) -> (leading: [BriefingSegmentRenderModel], trailing: [BriefingSegmentRenderModel]) {
    let weights = segments.map {
        briefingSegmentWeight(textLength: $0.segment.narrationText.count, isCard: $0.card != nil)
    }
    let columns = briefingSpreadColumnIndices(weights: weights)
    return (columns.leading.map { segments[$0] }, columns.trailing.map { segments[$0] })
}

/// Greedy balance: each next item goes to the column that is shorter so far
/// (ties to the leading column), preserving order within each column.
func briefingSpreadColumnIndices(weights: [Int]) -> (leading: [Int], trailing: [Int]) {
    var leading: [Int] = []
    var trailing: [Int] = []
    var leadingHeight = 0
    var trailingHeight = 0
    for (index, weight) in weights.enumerated() {
        if leadingHeight <= trailingHeight {
            leading.append(index)
            leadingHeight += weight
        } else {
            trailing.append(index)
            trailingHeight += weight
        }
    }
    return (leading, trailing)
}

/// Cards carry a hero image that is roughly as tall as 900 characters of
/// summary; every segment pays a fixed share for its spacing.
func briefingSegmentWeight(textLength: Int, isCard: Bool) -> Int {
    textLength + (isCard ? 900 : 0) + 120
}

extension View {
    /// Runs this pane screen to the trailing screen edge on the open display,
    /// where the status bar and camera float over content. No-op elsewhere.
    func extendsToOpenDisplayEdge() -> some View {
        modifier(OpenDisplayEdgeExtension())
    }
}

private struct OpenDisplayEdgeExtension: ViewModifier {
    @Environment(\.isInOpenDisplayPane) private var isInOpenDisplayPane

    func body(content: Content) -> some View {
        content.ignoresSafeArea(.container, edges: isInOpenDisplayPane ? .trailing : [])
    }
}
