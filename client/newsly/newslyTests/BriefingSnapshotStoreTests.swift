import XCTest
@testable import newsly

final class BriefingSnapshotStoreTests: XCTestCase {
    func testSnapshotStoreIsPerUserAndRejectsExpiredData() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let userOneStore = BriefingSnapshotStore(userID: 1, directory: directory)
        let userTwoStore = BriefingSnapshotStore(userID: 2, directory: directory)
        let fresh = BriefingSnapshot(
            userID: 1,
            index: makeIndex(lenses: [makeLensSummary(key: "today")]),
            etag: "etag-1",
            selectedLensKey: "today",
            lenses: ["today": makeLens(key: "today")],
            savedAt: Date()
        )
        await userOneStore.save(fresh)

        let userOneSnapshot = await userOneStore.load()
        let userTwoSnapshot = await userTwoStore.load()
        XCTAssertNotNil(userOneSnapshot)
        XCTAssertNil(userTwoSnapshot)

        let expired = BriefingSnapshot(
            userID: 1,
            index: fresh.index,
            etag: fresh.etag,
            selectedLensKey: fresh.selectedLensKey,
            lenses: fresh.lenses,
            savedAt: Date(timeIntervalSinceNow: -(60 * 60 * 49))
        )
        await userOneStore.save(expired)

        let expiredSnapshot = await userOneStore.load()
        XCTAssertNil(expiredSnapshot)
    }

    func testLogoutInvalidationDeletesSnapshotsAndRejectsWritesFromOldStores() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let oldStore = BriefingSnapshotStore(userID: 1, directory: directory)
        let snapshot = BriefingSnapshot(
            userID: 1,
            index: makeIndex(version: 1, lenses: [makeLensSummary(key: "today")]),
            etag: "etag-1",
            selectedLensKey: "today",
            lenses: ["today": makeLens(key: "today")],
            savedAt: Date()
        )
        await oldStore.save(snapshot)
        let snapshotURL = directory
            .appendingPathComponent("Briefing", isDirectory: true)
            .appendingPathComponent("1", isDirectory: true)
            .appendingPathComponent("snapshot.json")
        XCTAssertTrue(FileManager.default.fileExists(atPath: snapshotURL.path))

        BriefingSnapshotStore.invalidateAllSnapshots(in: directory)
        await oldStore.save(snapshot)

        XCTAssertFalse(FileManager.default.fileExists(atPath: snapshotURL.path))

        let currentStore = BriefingSnapshotStore(userID: 1, directory: directory)
        await currentStore.save(snapshot)
        await oldStore.clear()
        let restored = await currentStore.load()
        XCTAssertEqual(restored?.index.version, 1)
    }
}
