import XCTest
@testable import newsly

final class TabCoordinatorViewModelTests: XCTestCase {
    func testRootTabsContainOnlyBriefingCompositionSurfaces() {
        XCTAssertEqual(RootTab.allCases, [.briefing, .knowledge, .learning])
    }

    func testRootTabLogNamesMatchActiveSurfaces() {
        XCTAssertEqual(RootTab.briefing.logName, "briefing")
        XCTAssertEqual(RootTab.knowledge.logName, "knowledge")
        XCTAssertEqual(RootTab.learning.logName, "learning")
    }

    @MainActor
    func testInjectedContentRoutesUseBriefingNavigationForEveryContentType() {
        for contentType in APIContentType.knownCases {
            let route = E2ERouteInjector.contentRoute(
                contentId: 42,
                rawContentType: contentType.rawValue
            )

            XCTAssertEqual(route.contentId, 42)
            XCTAssertEqual(route.contentType, contentType)
            XCTAssertEqual(route.allContentIds, [42])
            XCTAssertEqual(route.navigationSurface, .briefing)
        }
    }

    @MainActor
    func testInjectedContentRouteDefaultsToNewsInsideBriefing() {
        let route = E2ERouteInjector.contentRoute(contentId: 7, rawContentType: nil)

        XCTAssertEqual(route.contentType, .news)
        XCTAssertEqual(route.navigationSurface, .briefing)
    }
}
