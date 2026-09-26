import XCTest
@testable import newsly

@MainActor
final class XConnectionStoreTests: XCTestCase {
    func testRefreshPublishesReauthRequiredAsNeedingAttention() async {
        let store = XConnectionStore(fetchConnection: { Self.connection(status: "reauth_required") })

        await store.refresh()

        XCTAssertTrue(store.needsAttention)
    }

    func testRefreshClearsAttentionOnceReconnected() async {
        var response = Self.connection(status: "reauth_required")
        let store = XConnectionStore(fetchConnection: { response })
        await store.refresh()

        response = Self.connection(status: "success", connected: true)
        await store.refresh()

        XCTAssertFalse(store.needsAttention)
    }

    func testFailedRefreshKeepsLastKnownConnection() async {
        var shouldFail = false
        let store = XConnectionStore(fetchConnection: {
            if shouldFail { throw URLError(.notConnectedToInternet) }
            return Self.connection(status: "reauth_required")
        })
        await store.refresh()

        shouldFail = true
        await store.refresh()

        XCTAssertTrue(store.needsAttention)
    }

    func testConcurrentRefreshesShareOneRequest() async {
        var fetchCount = 0
        let store = XConnectionStore(fetchConnection: {
            fetchCount += 1
            await Task.yield()
            return Self.connection(status: "success", connected: true)
        })

        async let first: Void = store.refresh()
        async let second: Void = store.refresh()
        _ = await (first, second)

        XCTAssertEqual(fetchCount, 1)
    }

    private static func connection(status: String, connected: Bool = false) -> XConnectionResponse {
        XConnectionResponse(
            provider: "x",
            connected: connected,
            isActive: connected,
            providerUserID: "123",
            providerUsername: "willemaw",
            scopes: [],
            lastSyncedAt: nil,
            lastStatus: status,
            lastError: nil
        )
    }
}
