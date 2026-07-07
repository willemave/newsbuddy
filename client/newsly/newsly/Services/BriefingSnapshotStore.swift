//
//  BriefingSnapshotStore.swift
//  newsly
//
//  Persists the last briefing so the tab paints instantly on cold start;
//  the view model revalidates against the server (ETag) in the background.
//

import Foundation

struct BriefingSnapshot: Codable {
    let index: APIBriefingIndexResponse
    let etag: String?
    let lenses: [String: APIBriefingLensResponse]
    let savedAt: Date
}

protocol BriefingSnapshotStoring: AnyObject {
    func load() -> BriefingSnapshot?
    func save(_ snapshot: BriefingSnapshot)
    func clear()
}

final class BriefingSnapshotStore: BriefingSnapshotStoring {
    static let shared = BriefingSnapshotStore()

    /// Briefings regenerate daily; anything older than this is not worth
    /// flashing before the fresh fetch replaces it.
    private static let maxSnapshotAge: TimeInterval = 60 * 60 * 48

    private let fileURL: URL
    private let queue = DispatchQueue(label: "com.newsly.briefing-snapshot", qos: .utility)
    private var logoutObserver: NSObjectProtocol?

    init(directory: URL? = nil) {
        let baseDirectory = directory ?? FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        self.fileURL = baseDirectory.appendingPathComponent("briefing-snapshot.json")
        logoutObserver = NotificationCenter.default.addObserver(
            forName: .authDidLogOut,
            object: nil,
            queue: nil
        ) { [weak self] _ in
            self?.clear()
        }
    }

    deinit {
        if let logoutObserver {
            NotificationCenter.default.removeObserver(logoutObserver)
        }
    }

    func load() -> BriefingSnapshot? {
        guard let data = try? Data(contentsOf: fileURL),
              let snapshot = try? JSONDecoder().decode(BriefingSnapshot.self, from: data)
        else { return nil }
        guard AppClock.now.timeIntervalSince(snapshot.savedAt) < Self.maxSnapshotAge else {
            clear()
            return nil
        }
        return snapshot
    }

    func save(_ snapshot: BriefingSnapshot) {
        queue.async { [fileURL] in
            guard let data = try? JSONEncoder().encode(snapshot) else { return }
            try? FileManager.default.createDirectory(
                at: fileURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try? data.write(to: fileURL, options: .atomic)
        }
    }

    func clear() {
        queue.async { [fileURL] in
            try? FileManager.default.removeItem(at: fileURL)
        }
    }
}
