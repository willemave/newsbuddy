import OSLog

private let briefingFirstRunLogger = Logger(
    subsystem: "com.newsly",
    category: "BriefingFirstRun"
)

@MainActor
final class BriefingFirstRunCoordinator {
    private enum TaskKey: Hashable {
        case polling
        case completion
    }

    private let service: BriefingServicing
    private let completionRetryDelay: UInt64
    private let pollingDelay: UInt64
    private let tasks = TaskBag<TaskKey>()

    init(
        service: BriefingServicing,
        completionRetryDelay: UInt64,
        pollingDelay: UInt64 = 1_500_000_000
    ) {
        self.service = service
        self.completionRetryDelay = completionRetryDelay
        self.pollingDelay = pollingDelay
    }

    func setPolling(_ enabled: Bool, refresh: @escaping @MainActor () async -> Void) {
        guard enabled else {
            tasks.cancel(.polling)
            return
        }
        let pollingDelay = pollingDelay
        tasks.runIfIdle(.polling) {
            while !Task.isCancelled {
                do {
                    try await Task.sleep(nanoseconds: pollingDelay)
                } catch {
                    return
                }
                await refresh()
            }
        }
    }

    func stopPolling() {
        tasks.cancel(.polling)
    }

    func complete() {
        tasks.cancel(.polling)
        let service = service
        let completionRetryDelay = completionRetryDelay
        tasks.runReplacing(.completion) {
            while !Task.isCancelled {
                do {
                    try await service.completeFirstRun()
                    return
                } catch {
                    briefingFirstRunLogger.error(
                        "Failed to persist Start Here completion; retrying: \(error.localizedDescription, privacy: .private)"
                    )
                }
                do {
                    try await Task.sleep(nanoseconds: completionRetryDelay)
                } catch {
                    return
                }
            }
        }
    }
}
