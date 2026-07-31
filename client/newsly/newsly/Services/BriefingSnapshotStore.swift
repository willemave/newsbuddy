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
    let lastValidatedAt: Date?
    let savedAt: Date

    init(
        userID: Int,
        index: APIBriefingIndexResponse,
        etag: String?,
        selectedLensKey: String?,
        lenses: [String: APIBriefingLensResponse],
        lastValidatedAt: Date? = nil,
        savedAt: Date
    ) {
        self.schemaVersion = Self.currentSchemaVersion
        self.userID = userID
        self.index = index
        self.etag = etag
        self.selectedLensKey = selectedLensKey
        self.lenses = lenses
        self.lastValidatedAt = lastValidatedAt
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
    /// Displaying an older briefing is preferable to replacing the whole screen
    /// with a transport failure. Freshness is tracked separately by the view
    /// model, and successful revalidation replaces this stale fallback.
    private static let maxDisplayAge: TimeInterval = 60 * 60 * 24 * 7

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
        guard let data else {
            briefingSnapshotLogger.info(
                "Snapshot cache miss | reason=file_unavailable user_id=\(self.userID, privacy: .private) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public)"
            )
            return nil
        }
        guard let decoded = try? JSONDecoder().decode(BriefingSnapshot.self, from: data) else {
            briefingSnapshotLogger.info(
                "Snapshot cache miss | reason=decode_failed user_id=\(self.userID, privacy: .private) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public)"
            )
            return nil
        }
        guard decoded.schemaVersion == BriefingSnapshot.currentSchemaVersion else {
            briefingSnapshotLogger.info(
                "Snapshot cache miss | reason=schema_mismatch user_id=\(self.userID, privacy: .private) schema=\(decoded.schemaVersion, privacy: .public)"
            )
            return nil
        }
        guard decoded.userID == userID else {
            briefingSnapshotLogger.info(
                "Snapshot cache miss | reason=user_mismatch user_id=\(self.userID, privacy: .private)"
            )
            return nil
        }
        let age = AppClock.now.timeIntervalSince(decoded.savedAt)
        guard age < Self.maxDisplayAge else {
            briefingSnapshotLogger.info(
                "Snapshot cache miss | reason=display_expired user_id=\(self.userID, privacy: .private) age_seconds=\(Int(age), privacy: .public)"
            )
            return nil
        }
        guard let snapshot = Self.storageLock.accessIfCurrent(
            storageGeneration,
            operation: { decoded }
        ) else {
            briefingSnapshotLogger.info(
                "Snapshot cache miss | reason=invalidated user_id=\(self.userID, privacy: .private)"
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
