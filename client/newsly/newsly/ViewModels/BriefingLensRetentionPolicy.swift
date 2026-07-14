import Foundation

private let briefingLensRetentionGraceNanoseconds: UInt64 = 30_000_000_000

@MainActor
protocol BriefingLensRetentionScheduling: AnyObject {
    func scheduleExpiry(
        for lensKey: String,
        action: @escaping @MainActor () -> Void
    )
    func cancelExpiry(for lensKey: String)
}

@MainActor
final class BriefingLensRetentionScheduler: BriefingLensRetentionScheduling {
    private let graceNanoseconds: UInt64
    private let tasks = TaskBag<String>()

    init(graceNanoseconds: UInt64 = briefingLensRetentionGraceNanoseconds) {
        self.graceNanoseconds = graceNanoseconds
    }

    deinit {
        tasks.cancelAll()
    }

    func scheduleExpiry(
        for lensKey: String,
        action: @escaping @MainActor () -> Void
    ) {
        tasks.runReplacing(lensKey) { [graceNanoseconds] in
            do {
                try await Task.sleep(nanoseconds: graceNanoseconds)
            } catch {
                return
            }
            guard !Task.isCancelled else { return }
            action()
        }
    }

    func cancelExpiry(for lensKey: String) {
        tasks.cancel(lensKey)
    }
}

@MainActor
final class BriefingLensRetentionPolicy {
    private enum Status {
        case retained
        case expired
    }

    private let scheduler: any BriefingLensRetentionScheduling
    private var statuses: [String: Status] = [:]

    init(scheduler: any BriefingLensRetentionScheduling) {
        self.scheduler = scheduler
    }

    func isRetained(_ lensKey: String) -> Bool {
        statuses[lensKey] == .retained
    }

    func isExpired(_ lensKey: String) -> Bool {
        statuses[lensKey] == .expired
    }

    func beginRetaining(
        _ lensKey: String,
        onExpiry: @escaping @MainActor () -> Void
    ) {
        statuses[lensKey] = .retained
        scheduler.scheduleExpiry(for: lensKey) { [weak self] in
            guard let self, self.statuses[lensKey] == .retained else { return }
            self.statuses[lensKey] = .expired
            onExpiry()
        }
    }

    func protect(_ lensKey: String) {
        statuses.removeValue(forKey: lensKey)
        scheduler.cancelExpiry(for: lensKey)
    }
}
