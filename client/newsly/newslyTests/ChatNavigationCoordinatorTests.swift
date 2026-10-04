import XCTest
@testable import newsly

@MainActor
final class ChatNavigationCoordinatorTests: XCTestCase {
    func testOpenStoresPendingRouteAndClearRemovesMatchingRoute() {
        let coordinator = ChatNavigationCoordinator()

        let firstRoute = ChatSessionRoute(sessionId: 42)
        let secondRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(firstRoute)

        XCTAssertEqual(coordinator.queuedRoute, firstRoute)

        coordinator.clear(route: secondRoute)
        XCTAssertEqual(coordinator.queuedRoute, firstRoute)

        coordinator.clear(route: firstRoute)
        XCTAssertNil(coordinator.queuedRoute)
    }

    func testConsecutiveRoutesAreDeliveredInOrderInsteadOfOverwritten() {
        let coordinator = ChatNavigationCoordinator()
        let firstRoute = ChatSessionRoute(sessionId: 42)
        let secondRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(firstRoute)
        coordinator.open(secondRoute)

        XCTAssertEqual(coordinator.queuedRoute, firstRoute)
        XCTAssertTrue(coordinator.beginPresentation(firstRoute))
        XCTAssertEqual(coordinator.presentedRoute, firstRoute)
        XCTAssertEqual(coordinator.queuedRoute, secondRoute)
        XCTAssertTrue(coordinator.acknowledgePresented(firstRoute))
        XCTAssertNil(coordinator.presentedRoute)
        XCTAssertEqual(coordinator.queuedRoute, secondRoute)
        XCTAssertTrue(coordinator.beginPresentation(secondRoute))
        XCTAssertTrue(coordinator.acknowledgePresented(secondRoute))
        XCTAssertNil(coordinator.presentedRoute)
        XCTAssertNil(coordinator.queuedRoute)
    }

    func testDuplicatePendingRouteIsCoalesced() {
        let coordinator = ChatNavigationCoordinator()
        let route = ChatSessionRoute(sessionId: 42)

        coordinator.open(route)
        coordinator.open(route)
        coordinator.clear(route: route)

        XCTAssertNil(coordinator.queuedRoute)
    }

    func testAcknowledgementOnlyRemovesThePresentedHeadRoute() {
        let coordinator = ChatNavigationCoordinator()
        let firstRoute = ChatSessionRoute(sessionId: 42)
        let secondRoute = ChatSessionRoute(sessionId: 99)
        coordinator.open(firstRoute)
        coordinator.open(secondRoute)

        XCTAssertFalse(coordinator.acknowledgePresented(secondRoute))
        XCTAssertEqual(coordinator.queuedRoute, firstRoute)
        XCTAssertTrue(coordinator.beginPresentation(firstRoute))
        XCTAssertEqual(coordinator.presentedRoute, firstRoute)
        XCTAssertEqual(coordinator.queuedRoute, secondRoute)
        XCTAssertFalse(coordinator.acknowledgePresented(secondRoute))
        XCTAssertEqual(coordinator.presentedRoute, firstRoute)
        XCTAssertTrue(coordinator.acknowledgePresented(firstRoute))
        XCTAssertNil(coordinator.presentedRoute)
        XCTAssertEqual(coordinator.queuedRoute, secondRoute)
    }

    func testOpeningRouteWhileAnotherIsPresentedQueuesWithoutReplacingIt() {
        let coordinator = ChatNavigationCoordinator()
        let visibleRoute = ChatSessionRoute(sessionId: 42)
        let queuedRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(visibleRoute)
        XCTAssertTrue(coordinator.beginPresentation(visibleRoute))
        coordinator.open(queuedRoute)

        XCTAssertEqual(coordinator.presentedRoute, visibleRoute)
        XCTAssertEqual(coordinator.queuedRoute, queuedRoute)

        XCTAssertTrue(coordinator.acknowledgePresented(visibleRoute))
        XCTAssertNil(coordinator.presentedRoute)
        XCTAssertEqual(coordinator.queuedRoute, queuedRoute)
    }

    func testQueuedRouteCanExplicitlyReplaceABackgroundPresentation() {
        let coordinator = ChatNavigationCoordinator()
        let visibleRoute = ChatSessionRoute(sessionId: 42)
        let requestedRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(visibleRoute)
        XCTAssertTrue(coordinator.beginPresentation(visibleRoute))
        coordinator.open(requestedRoute)

        XCTAssertTrue(
            coordinator.beginPresentation(
                requestedRoute,
                replacingPresented: true
            )
        )
        XCTAssertEqual(coordinator.presentedRoute, requestedRoute)
        XCTAssertNil(coordinator.queuedRoute)
    }

    func testContentDetailRouteCarriesNavigationReplacementIntentUntilPresentation() {
        let coordinator = ChatNavigationCoordinator()
        let route = ChatSessionRoute(sessionId: 42)

        coordinator.openReplacingCurrentNavigation(route)

        XCTAssertEqual(coordinator.queuedRoute, route)
        XCTAssertTrue(coordinator.queuedRouteReplacesCurrentNavigation)
        XCTAssertTrue(coordinator.beginPresentation(route, replacingPresented: true))
        XCTAssertFalse(coordinator.queuedRouteReplacesCurrentNavigation)
    }
}
