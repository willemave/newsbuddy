import XCTest
@testable import newsly

@MainActor
final class ReadingStateStoreTests: XCTestCase {
    private var defaults: UserDefaults!
    private var suiteName: String!

    override func setUp() {
        super.setUp()
        suiteName = "ReadingStateStoreTests.\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suiteName) else {
            fatalError("Failed to create isolated user defaults suite")
        }
        self.defaults = defaults
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        if let suiteName {
            defaults.removePersistentDomain(forName: suiteName)
        }
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    func testUserScopedStoreIgnoresLegacyReadingState() throws {
        let staleContentId = 299
        let currentUserId = 15
        let staleState = ReadingState(
            contentId: staleContentId,
            contentType: .article,
            lastUpdated: Date(timeIntervalSince1970: 1_771_654_473)
        )
        defaults.set(try JSONEncoder().encode(staleState), forKey: "currentReadingState")

        let store = ReadingStateStore(userId: currentUserId, defaults: defaults)

        XCTAssertNil(store.current)
    }

    func testUserScopedStoresDoNotShareReadingState() {
        let previousUserId = 8
        let currentUserId = 15
        let staleContentId = 299
        let previousUserStore = ReadingStateStore(userId: previousUserId, defaults: defaults)
        previousUserStore.setCurrent(contentId: staleContentId, type: .article)

        let latestUserStore = ReadingStateStore(userId: currentUserId, defaults: defaults)

        XCTAssertNil(latestUserStore.current)
        XCTAssertEqual(
            ReadingStateStore(userId: previousUserId, defaults: defaults).current?.contentId,
            staleContentId
        )
    }
}
