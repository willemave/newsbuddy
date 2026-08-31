import XCTest
@testable import newsly

@MainActor
final class ChatNavigationCoordinatorTests: XCTestCase {
    func testOpenStoresPendingRouteAndClearRemovesMatchingRoute() {
        let coordinator = ChatNavigationCoordinator()

        let firstRoute = ChatSessionRoute(sessionId: 42)
        let secondRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(firstRoute)

        XCTAssertEqual(coordinator.pendingRoute, firstRoute)

        coordinator.clear(route: secondRoute)
        XCTAssertEqual(coordinator.pendingRoute, firstRoute)

        coordinator.clear(route: firstRoute)
        XCTAssertNil(coordinator.pendingRoute)
    }

    func testConsecutiveRoutesAreDeliveredInOrderInsteadOfOverwritten() {
        let coordinator = ChatNavigationCoordinator()
        let firstRoute = ChatSessionRoute(sessionId: 42)
        let secondRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(firstRoute)
        coordinator.open(secondRoute)

        XCTAssertEqual(coordinator.pendingRoute, firstRoute)
        XCTAssertTrue(coordinator.beginPresentation(firstRoute))
        XCTAssertEqual(coordinator.presentedRoute, firstRoute)
        XCTAssertNil(coordinator.pendingRoute)
        XCTAssertTrue(coordinator.acknowledgePresented(firstRoute))
        XCTAssertEqual(coordinator.pendingRoute, secondRoute)
        XCTAssertTrue(coordinator.beginPresentation(secondRoute))
        XCTAssertTrue(coordinator.acknowledgePresented(secondRoute))
        XCTAssertNil(coordinator.pendingRoute)
    }

    func testDuplicatePendingRouteIsCoalesced() {
        let coordinator = ChatNavigationCoordinator()
        let route = ChatSessionRoute(sessionId: 42)

        coordinator.open(route)
        coordinator.open(route)
        coordinator.clear(route: route)

        XCTAssertNil(coordinator.pendingRoute)
    }

    func testAcknowledgementOnlyRemovesThePresentedHeadRoute() {
        let coordinator = ChatNavigationCoordinator()
        let firstRoute = ChatSessionRoute(sessionId: 42)
        let secondRoute = ChatSessionRoute(sessionId: 99)
        coordinator.open(firstRoute)
        coordinator.open(secondRoute)

        XCTAssertFalse(coordinator.acknowledgePresented(secondRoute))
        XCTAssertEqual(coordinator.pendingRoute, firstRoute)
        XCTAssertTrue(coordinator.beginPresentation(firstRoute))
        XCTAssertNil(coordinator.pendingRoute)
        XCTAssertFalse(coordinator.acknowledgePresented(secondRoute))
        XCTAssertTrue(coordinator.acknowledgePresented(firstRoute))
        XCTAssertEqual(coordinator.pendingRoute, secondRoute)
    }

    func testOpeningRouteWhileAnotherIsPresentedQueuesWithoutReplacingIt() {
        let coordinator = ChatNavigationCoordinator()
        let visibleRoute = ChatSessionRoute(sessionId: 42)
        let queuedRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(visibleRoute)
        XCTAssertTrue(coordinator.beginPresentation(visibleRoute))
        coordinator.open(queuedRoute)

        XCTAssertEqual(coordinator.presentedRoute, visibleRoute)
        XCTAssertNil(coordinator.pendingRoute)
        XCTAssertEqual(coordinator.queuedRoute, queuedRoute)

        XCTAssertTrue(coordinator.acknowledgePresented(visibleRoute))
        XCTAssertEqual(coordinator.pendingRoute, queuedRoute)
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
