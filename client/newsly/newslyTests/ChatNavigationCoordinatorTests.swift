import XCTest
@testable import newsly

@MainActor
final class ChatNavigationCoordinatorTests: XCTestCase {
    override func tearDown() {
        ChatNavigationCoordinator.shared.clear()
        super.tearDown()
    }

    func testOpenStoresPendingRouteAndClearRemovesMatchingRoute() {
        let coordinator = ChatNavigationCoordinator.shared
        coordinator.clear()

        let firstRoute = ChatSessionRoute(sessionId: 42)
        let secondRoute = ChatSessionRoute(sessionId: 99)

        coordinator.open(firstRoute)

        XCTAssertEqual(coordinator.pendingRoute, firstRoute)

        coordinator.clear(route: secondRoute)
        XCTAssertEqual(coordinator.pendingRoute, firstRoute)

        coordinator.clear(route: firstRoute)
        XCTAssertNil(coordinator.pendingRoute)
    }
}
