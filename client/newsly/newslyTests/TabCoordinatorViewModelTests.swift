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
    func testSelectingActiveTabRequestsScrollToTopWithoutChangingSelection() {
        let coordinator = TabCoordinatorViewModel(
            briefingVM: BriefingViewModel(service: MockBriefingService()),
            initialTab: .briefing
        )

        coordinator.select(.knowledge)
        XCTAssertEqual(coordinator.selectedTab, .knowledge)
        XCTAssertEqual(coordinator.scrollToTopRequest(for: .knowledge), 0)

        coordinator.select(.knowledge)
        coordinator.select(.knowledge)

        XCTAssertEqual(coordinator.selectedTab, .knowledge)
        XCTAssertEqual(coordinator.scrollToTopRequest(for: .knowledge), 2)
        XCTAssertEqual(coordinator.scrollToTopRequest(for: .briefing), 0)
    }

    @MainActor
    func testSelectedTabRestoresPerUser() {
        let suiteName = "TabCoordinatorViewModelTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let first = TabCoordinatorViewModel(
            briefingVM: BriefingViewModel(service: MockBriefingService()),
            userID: 7,
            defaults: defaults
        )
        first.selectedTab = .knowledge

        let restored = TabCoordinatorViewModel(
            briefingVM: BriefingViewModel(service: MockBriefingService()),
            userID: 7,
            defaults: defaults
        )
        let otherUser = TabCoordinatorViewModel(
            briefingVM: BriefingViewModel(service: MockBriefingService()),
            userID: 8,
            defaults: defaults
        )

        XCTAssertEqual(restored.selectedTab, .knowledge)
        XCTAssertEqual(otherUser.selectedTab, .briefing)
    }

    @MainActor
    func testExplicitInitialTabOverridesRestoredTab() {
        let suiteName = "TabCoordinatorViewModelTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defaults.set(RootTab.knowledge.rawValue, forKey: "root.selectedTab.user.7")
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let coordinator = TabCoordinatorViewModel(
            briefingVM: BriefingViewModel(service: MockBriefingService()),
            userID: 7,
            defaults: defaults,
            initialTab: .learning
        )

        XCTAssertEqual(coordinator.selectedTab, .learning)
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
