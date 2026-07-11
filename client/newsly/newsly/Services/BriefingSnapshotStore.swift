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

struct BriefingSnapshot: Codable {
    static let currentSchemaVersion = 2

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

final class BriefingSnapshotStore: BriefingSnapshotStoring {
    /// Briefings regenerate daily; older snapshots are not useful enough to
    /// flash before the fresh index replaces them.
    private static let maxSnapshotAge: TimeInterval = 60 * 60 * 48

    let userID: Int

    private let fileURL: URL
    private let queue: DispatchQueue
    private var logoutObserver: NSObjectProtocol?

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
        self.queue = DispatchQueue(
            label: "com.newsly.briefing-snapshot.\(userID)",
            qos: .utility
        )
        logoutObserver = NotificationCenter.default.addObserver(
            forName: .authDidLogOut,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            Task { await self?.clear() }
        }
    }

    deinit {
        if let logoutObserver {
            NotificationCenter.default.removeObserver(logoutObserver)
        }
    }

    func load() async -> BriefingSnapshot? {
        await withCheckedContinuation {
            (continuation: CheckedContinuation<BriefingSnapshot?, Never>) in
            queue.async { [fileURL, userID] in
                let startedAt = Date()
                guard let data = try? Data(contentsOf: fileURL),
                      let snapshot = try? JSONDecoder().decode(BriefingSnapshot.self, from: data),
                      snapshot.schemaVersion == BriefingSnapshot.currentSchemaVersion,
                      snapshot.userID == userID,
                      AppClock.now.timeIntervalSince(snapshot.savedAt) < Self.maxSnapshotAge
                else {
                    briefingSnapshotLogger.info(
                        "Snapshot cache miss | user_id=\(userID, privacy: .private) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public)"
                    )
                    continuation.resume(returning: nil)
                    return
                }
                briefingSnapshotLogger.info(
                    "Snapshot loaded | user_id=\(userID, privacy: .private) bytes=\(data.count, privacy: .public) lenses=\(snapshot.lenses.count, privacy: .public) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public)"
                )
                continuation.resume(returning: snapshot)
            }
        }
    }

    func save(_ snapshot: BriefingSnapshot) async {
        guard snapshot.userID == userID else { return }
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            queue.async { [fileURL] in
                let startedAt = Date()
                defer { continuation.resume() }
                guard let data = try? JSONEncoder().encode(snapshot) else { return }
                try? FileManager.default.createDirectory(
                    at: fileURL.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                try? data.write(to: fileURL, options: .atomic)
                briefingSnapshotLogger.info(
                    "Snapshot saved | user_id=\(snapshot.userID, privacy: .private) bytes=\(data.count, privacy: .public) lenses=\(snapshot.lenses.count, privacy: .public) duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public)"
                )
            }
        }
    }

    func clear() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            queue.async { [fileURL] in
                try? FileManager.default.removeItem(at: fileURL)
                continuation.resume()
            }
        }
    }
}
