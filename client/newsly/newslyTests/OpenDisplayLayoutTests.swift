import SwiftUI
import XCTest
@testable import newsly

final class OpenDisplayLayoutTests: XCTestCase {
    // MARK: Root layout gate

    func testOnlyRegularInBothDirectionsUsesOpenDisplayLayout() {
        XCTAssertEqual(RootLayout.resolve(horizontal: .regular, vertical: .regular), .openDisplay)
        // Pro Max landscape.
        XCTAssertEqual(RootLayout.resolve(horizontal: .regular, vertical: .compact), .compact)
        // Every iPhone in portrait and the closed Duo.
        XCTAssertEqual(RootLayout.resolve(horizontal: .compact, vertical: .regular), .compact)
        XCTAssertEqual(RootLayout.resolve(horizontal: .compact, vertical: .compact), .compact)
        XCTAssertEqual(RootLayout.resolve(horizontal: nil, vertical: .regular), .compact)
    }

    // MARK: Briefing arrangement

    func testNewsWithNothingOpenFillsTheViewInOneColumn() {
        XCTAssertEqual(
            OpenBriefingArrangement.resolve(isNewsLike: true, hasStory: false, hasChat: false),
            .newsAlone
        )
    }

    func testNewsStoriesAlwaysOpenSideBySide() {
        XCTAssertEqual(
            OpenBriefingArrangement.resolve(isNewsLike: true, hasStory: true, hasChat: false),
            .newsBeside(.detail)
        )
    }

    func testAttachedChatTakesAPageNeverAnOverlay() {
        XCTAssertEqual(
            OpenBriefingArrangement.resolve(isNewsLike: true, hasStory: true, hasChat: true),
            .chatBesideStory
        )
        XCTAssertEqual(
            OpenBriefingArrangement.resolve(isNewsLike: true, hasStory: false, hasChat: true),
            .newsBeside(.chat)
        )
        XCTAssertEqual(
            OpenBriefingArrangement.resolve(isNewsLike: false, hasStory: true, hasChat: true),
            .listBeside(.chat)
        )
    }

    func testListTiersShowDetailOrInterstitialBesideTheSidebar() {
        XCTAssertEqual(
            OpenBriefingArrangement.resolve(isNewsLike: false, hasStory: false, hasChat: false),
            .listBeside(.interstitial)
        )
        XCTAssertEqual(
            OpenBriefingArrangement.resolve(isNewsLike: false, hasStory: true, hasChat: false),
            .listBeside(.detail)
        )
    }

    func testNewsBesideAStoryEndsFortyPointsBeforeTheHinge() {
        let hinge = OpenDisplayMetrics.hingeX(
            foldFrame: CGRect(x: 402, y: 0, width: 40, height: 669),
            contentHeight: 669
        )
        XCTAssertEqual(hinge, 422)
        XCTAssertEqual(OpenDisplayMetrics.newsLeadingWidth(hingeX: hinge, contentWidth: 898), 382)
        // A horizontal fold (upright hold) has no hinge line to measure from.
        XCTAssertNil(OpenDisplayMetrics.hingeX(
            foldFrame: CGRect(x: 0, y: 430, width: 617, height: 40),
            contentHeight: 899
        ))
        XCTAssertEqual(OpenDisplayMetrics.newsLeadingWidth(hingeX: nil, contentWidth: 617), 300)
        XCTAssertEqual(OpenDisplayMetrics.newsLeadingWidth(hingeX: nil, contentWidth: 900), 378, accuracy: 0.001)
    }

    // MARK: Rail selection

    func testRailReflectsBriefingSelection() {
        XCTAssertEqual(
            OpenRailDestination.current(
                selectedTab: .briefing,
                isStartHereSelected: true,
                isNewsTierSelected: false,
                selectedLensKey: nil
            ),
            .startHere
        )
        XCTAssertEqual(
            OpenRailDestination.current(
                selectedTab: .briefing,
                isStartHereSelected: false,
                isNewsTierSelected: true,
                selectedLensKey: "news-markets"
            ),
            .news
        )
        XCTAssertEqual(
            OpenRailDestination.current(
                selectedTab: .briefing,
                isStartHereSelected: false,
                isNewsTierSelected: false,
                selectedLensKey: "podcasts"
            ),
            .lens("podcasts")
        )
    }

    func testRailShowsOneKnowledgeDestination() {
        XCTAssertEqual(
            OpenRailDestination.current(
                selectedTab: .knowledge,
                isStartHereSelected: false,
                isNewsTierSelected: true,
                selectedLensKey: "news-markets"
            ),
            .knowledge
        )
    }

    // MARK: Selection across layouts

    private func route(_ contentId: Int, ids: [Int] = [10, 11, 12]) -> ContentDetailRoute {
        ContentDetailRoute(
            contentId: contentId,
            contentType: .article,
            allContentIds: ids,
            navigationSurface: .briefing,
            initialScrollTarget: .comments
        )
    }

    func testRetargetFollowsThePagedItem() {
        let retargeted = retargetedBriefingSelection(route(10), visibleContentId: 12)

        XCTAssertEqual(retargeted.contentId, 12)
        XCTAssertEqual(retargeted.allContentIds, [10, 11, 12])
        XCTAssertEqual(retargeted.navigationSurface, .briefing)
        // The opened item's scroll target does not carry to another item.
        XCTAssertNil(retargeted.initialScrollTarget)
    }

    func testRetargetKeepsRouteWhenNothingPagedOrIdIsForeign() {
        let original = route(10)

        XCTAssertEqual(retargetedBriefingSelection(original, visibleContentId: nil), original)
        XCTAssertEqual(retargetedBriefingSelection(original, visibleContentId: 10), original)
        XCTAssertEqual(retargetedBriefingSelection(original, visibleContentId: 99), original)
    }

    // MARK: Learning Deck reader layout

    func testDeckReaderLandscapeOnPhonesFollowsCompactHeight() {
        XCTAssertTrue(learningDeckUsesLandscapeLayout(
            horizontal: .compact,
            vertical: .compact,
            keyboardIndependentSize: CGSize(width: 874, height: 402)
        ))
        // Keyboard-shrunk portrait phone stays portrait.
        XCTAssertFalse(learningDeckUsesLandscapeLayout(
            horizontal: .compact,
            vertical: .regular,
            keyboardIndependentSize: CGSize(width: 402, height: 300)
        ))
    }

    func testDeckReaderOnOpenDisplayFollowsItsShape() {
        XCTAssertTrue(learningDeckUsesLandscapeLayout(
            horizontal: .regular,
            vertical: .regular,
            keyboardIndependentSize: CGSize(width: 890, height: 626)
        ))
        XCTAssertFalse(learningDeckUsesLandscapeLayout(
            horizontal: .regular,
            vertical: .regular,
            keyboardIndependentSize: CGSize(width: 626, height: 890)
        ))
        XCTAssertFalse(learningDeckUsesLandscapeLayout(
            horizontal: .regular,
            vertical: .regular,
            keyboardIndependentSize: .zero
        ))
    }

    // MARK: Rows and interstitials

    func testRowSummaryLineIsTheFirstSentence() {
        XCTAssertEqual(
            BriefingSourceListRow.summaryLine("Cornelis is moving beyond networking. Spellman says more."),
            "Cornelis is moving beyond networking."
        )
        XCTAssertEqual(BriefingSourceListRow.summaryLine("  no ending  "), "no ending")
        XCTAssertNil(BriefingSourceListRow.summaryLine("   "))
    }

    func testInterstitialMetaCountsAndDuration() {
        XCTAssertEqual(
            openDisplayInterstitialMeta(isPodcasts: true, unreadCount: 73, totalSeconds: 61 * 3600),
            "73 episodes waiting · about 61 hours"
        )
        XCTAssertEqual(
            openDisplayInterstitialMeta(isPodcasts: false, unreadCount: 9, totalSeconds: 100 * 60),
            "9 unread · about 1 hr 40 min of reading"
        )
        XCTAssertEqual(openDisplayInterstitialMeta(isPodcasts: true, unreadCount: 1, totalSeconds: 0), "1 episode waiting")
        XCTAssertNil(openDisplayInterstitialMeta(isPodcasts: false, unreadCount: 0, totalSeconds: 600))
    }

    func testChatHistoryGroupsByDayNewestFirst() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        func session(_ id: Int, _ date: Date) -> ChatSessionSummary {
            ChatSessionSummary.fixture(id: id, lastMessageAt: date)
        }
        let now = Date(timeIntervalSince1970: 1_800_000_000) // 08:00 UTC, away from midnight
        let groups = ChatHistoryDayGroup.group(
            [session(1, now.addingTimeInterval(-3600 * 72)), session(2, now), session(3, now.addingTimeInterval(-60))],
            calendar: calendar
        )
        XCTAssertEqual(groups.first?.sessions.map(\.id), [2, 3])
        XCTAssertEqual(groups.last?.sessions.map(\.id), [1])
    }
}
