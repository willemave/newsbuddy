import XCTest
@testable import newsly

final class AuthoritativeMutationLogTests: XCTestCase {
    private struct Item: Equatable {
        let id: Int
        let name: String
    }

    func testAuthoritativeLoadRetiresOlderUpsertEvenWhenItemIsAbsent() {
        var log = AuthoritativeMutationLog<Int, Item>()
        log.recordUpsert(Item(id: 1, name: "local"), id: 1)
        let requestRevision = log.currentRevision

        let first = log.reconcile(
            authoritative: [],
            requestStartRevision: requestRevision,
            id: \.id
        )
        let second = log.reconcile(
            authoritative: [Item(id: 2, name: "server")],
            requestStartRevision: log.currentRevision,
            id: \.id
        )

        XCTAssertEqual(first, [])
        XCTAssertEqual(second, [Item(id: 2, name: "server")])
    }

    func testMutationAfterRequestStartedReplacesInPlaceOrInsertsAtTop() {
        var log = AuthoritativeMutationLog<Int, Item>()
        let requestRevision = log.currentRevision
        log.recordUpsert(Item(id: 2, name: "updated"), id: 2)
        log.recordUpsert(Item(id: 3, name: "new"), id: 3)

        let reconciled = log.reconcile(
            authoritative: [Item(id: 1, name: "one"), Item(id: 2, name: "old")],
            requestStartRevision: requestRevision,
            id: \.id
        )

        XCTAssertEqual(
            reconciled,
            [Item(id: 3, name: "new"), Item(id: 1, name: "one"), Item(id: 2, name: "updated")]
        )
    }
}
