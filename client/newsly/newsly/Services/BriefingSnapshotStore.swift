//
//  BriefingSnapshotStore.swift
//  newsly
//
//  Persists a bounded, per-user Briefing working set. The snapshot is only a
//  cold-start cache; BriefingViewModel always revalidates it with the server.
//

import Foundation
import OSLog

private let briefingSnapshotLogger = Logger(subsystem: "com.newsly", category: "BriefingSnapshot")

private final class BriefingSnapshotStorageLock: @unchecked Sendable {
    private let lock = NSLock()
    private var generation = 0

    func currentGeneration() -> Int {
        lock.withLock { generation }
    }

    func accessIfCurrent<T>(
        _ expectedGeneration: Int,
        operation: () -> T?
    ) -> T? {
        lock.withLock {
            guard generation == expectedGeneration else { return nil }
            return operation()
        }
    }

    func invalidate(operation: () -> Void) {
        lock.withLock {
            generation += 1
            operation()
        }
    }
}

struct BriefingSnapshot: Codable {
    static let currentSchemaVersion = 3

    let schemaVersion: Int
    let userID: Int
    let index: APIBriefingIndexResponse
    let etag: String?
    let selectedLensKey: String?
    let lenses: [String: APIBriefingLensResponse]
    let savedAt: Date

    init(
        userID: Int,
        index: APIBriefingIndexResponse,
        etag: String?,
        selectedLensKey: String?,
        lenses: [String: APIBriefingLensResponse],
        savedAt: Date
    ) {
        self.schemaVersion = Self.currentSchemaVersion
        self.userID = userID
        self.index = index
        self.etag = etag
        self.selectedLensKey = selectedLensKey
        self.lenses = lenses
        self.savedAt = savedAt
    }
}

protocol BriefingSnapshotStoring: AnyObject {
    var userID: Int { get }

    func load() async -> BriefingSnapshot?
    func save(_ snapshot: BriefingSnapshot) async
    func clear() async
}

actor BriefingSnapshotStore: BriefingSnapshotStoring {
    /// Briefings regenerate daily; older snapshots are not useful enough to
    /// flash before the fresh index replaces them.
    private static let maxSnapshotAge: TimeInterval = 60 * 60 * 48

    private nonisolated static let storageLock = BriefingSnapshotStorageLock()

    nonisolated let userID: Int

    private let fileURL: URL
    private let storageGeneration: Int

    init(userID: Int, directory: URL? = nil) {
        self.userID = userID
        let baseDirectory = directory ?? FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        self.fileURL = baseDirectory
            .appendingPathComponent("Briefing", isDirectory: true)
            .appendingPathComponent(String(userID), isDirectory: true)
            .appendingPathComponent("snapshot.json")
        self.storageGeneration = Self.storageLock.currentGeneration()
    }

    nonisolated static func invalidateAllSnapshots(in directory: URL? = nil) {
        let baseDirectory = directory ?? FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        storageLock.invalidate {
            try? FileManager.default.removeItem(
                at: baseDirectory.appendingPathComponent("Briefing", isDirectory: true)
            )
        }
    }

    func load() async -> BriefingSnapshot? {
        let signpostState = BriefingPerformance.signposter.beginInterval("snapshot-read-decode")
        defer { BriefingPerformance.signposter.endInterval("snapshot-read-decode", signpostState) }
        let startedAt = Date()
        let data: Data? = Self.storageLock.accessIfCurrent(storageGeneration) {
            try? Data(contentsOf: self.fileURL)
        }
        let readFinishedAt = Date()
        guard let data,
              let decoded = try? JSONDecoder().decode(BriefingSnapshot.self, from: data),
              decoded.schemaVersion == BriefingSnapshot.currentSchemaVersion,
              decoded.userID == self.userID,
              AppClock.now.timeIntervalSince(decoded.savedAt) < Self.maxSnapshotAge,
              let snapshot = Self.storageLock.accessIfCurrent(
                  storageGeneration,
                  operation: { decoded }
              )
        else {
            briefingSnapshotLogger.info(
                "Snapshot cache miss | user_id=\(self.userID, privacy: .private) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public)"
            )
            return nil
        }
        briefingSnapshotLogger.info(
            "Snapshot loaded | user_id=\(self.userID, privacy: .private) bytes=\(data.count, privacy: .public) lenses=\(snapshot.lenses.count, privacy: .public) read_ms=\(Int(readFinishedAt.timeIntervalSince(startedAt) * 1_000), privacy: .public) decode_ms=\(Int(Date().timeIntervalSince(readFinishedAt) * 1_000), privacy: .public)"
        )
        return snapshot
    }

    func save(_ snapshot: BriefingSnapshot) async {
        let signpostState = BriefingPerformance.signposter.beginInterval("snapshot-encode-write")
        defer { BriefingPerformance.signposter.endInterval("snapshot-encode-write", signpostState) }
        guard snapshot.userID == userID else { return }
        let startedAt = Date()
        guard let encoded = try? JSONEncoder().encode(snapshot) else { return }
        let encodeFinishedAt = Date()
        let data: Data? = Self.storageLock.accessIfCurrent(storageGeneration) {
            try? FileManager.default.createDirectory(
                at: self.fileURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try? encoded.write(to: self.fileURL, options: .atomic)
            return encoded
        }
        guard let data else { return }
        briefingSnapshotLogger.info(
            "Snapshot saved | user_id=\(snapshot.userID, privacy: .private) bytes=\(data.count, privacy: .public) lenses=\(snapshot.lenses.count, privacy: .public) encode_ms=\(Int(encodeFinishedAt.timeIntervalSince(startedAt) * 1_000), privacy: .public) write_ms=\(Int(Date().timeIntervalSince(encodeFinishedAt) * 1_000), privacy: .public)"
        )
    }

    func clear() async {
        Self.storageLock.accessIfCurrent(storageGeneration) {
            try? FileManager.default.removeItem(at: self.fileURL)
        }
    }
}
