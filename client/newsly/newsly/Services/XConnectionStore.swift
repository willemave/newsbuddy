//
//  XConnectionStore.swift
//  newsly
//

import Foundation
import Observation

/// The authenticated session's view of the user's X connection.
///
/// Settings entry points read `needsAttention` so a broken connection is visible
/// without opening Settings. Nothing is persisted: server sync state stays the only
/// source of truth and is re-read on activation and after every connection change.
@MainActor
@Observable
final class XConnectionStore {
    typealias FetchConnection = @MainActor () async throws -> XConnectionResponse

    private(set) var connection: XConnectionResponse?

    var needsAttention: Bool {
        connection?.needsAttention == true
    }

    @ObservationIgnored
    private let fetchConnection: FetchConnection
    @ObservationIgnored
    private var refreshTask: Task<Void, Never>?

    init(fetchConnection: @escaping FetchConnection) {
        self.fetchConnection = fetchConnection
    }

    /// Coalesces concurrent callers onto one request. A failed fetch keeps the last
    /// known state so a network blip neither hides nor invents a connection problem.
    func refresh() async {
        if let refreshTask {
            await refreshTask.value
            return
        }

        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            if let connection = try? await self.fetchConnection() {
                self.connection = connection
            }
        }
        refreshTask = task
        await task.value
        refreshTask = nil
    }
}
