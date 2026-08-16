import Foundation
import OSLog

private let briefingIndexLogger = Logger(subsystem: "com.newsly", category: "BriefingRefresh")

let briefingRefreshPollDelaysNanoseconds: [UInt64] = [
    750_000_000,
    1_500_000_000,
    3_000_000_000,
    5_000_000_000,
    5_000_000_000,
    5_000_000_000,
    5_000_000_000,
    5_000_000_000,
]

@MainActor
final class BriefingIndexSynchronizer {
    enum RefreshPhase: Equatable {
        case idle
        case requesting
        case waitingForVersion
        case failed(String)
    }

    private enum TaskKey: Hashable {
        case activation
        case index
        case manualRefresh
        case refreshPoll
    }

    private enum LoadMode: Equatable {
        case conditional
        case forced
    }

    private let service: BriefingServicing
    private let refreshPollDelays: [UInt64]
    private let tasks = TaskBag<TaskKey>()

    private var isActive = false
    private var loadMode: LoadMode?
    private(set) var etag: String?
    private(set) var refreshPhase: RefreshPhase = .idle {
        didSet {
            guard oldValue != refreshPhase else { return }
            onRefreshPhaseChange?(refreshPhase)
        }
    }

    var onRefreshPhaseChange: ((RefreshPhase) -> Void)?

    init(
        service: BriefingServicing,
        refreshPollDelays: [UInt64] = briefingRefreshPollDelaysNanoseconds
    ) {
        self.service = service
        self.refreshPollDelays = refreshPollDelays
    }

    deinit {
        tasks.cancelAll()
    }

    func setActive(
        _ active: Bool,
        onActivation: @escaping @MainActor () async -> Void
    ) {
        guard isActive != active else { return }
        isActive = active
        briefingIndexLogger.info("Briefing activity changed | active=\(active, privacy: .public)")

        guard active else {
            tasks.cancelAll()
            loadMode = nil
            if refreshPhase != .idle {
                refreshPhase = .idle
            }
            return
        }

        tasks.runReplacing(.activation, operation: onActivation)
    }

    func restore(etag: String?) {
        self.etag = etag
    }

    func cancelIndexLoad() {
        tasks.cancel(.index)
        loadMode = nil
    }

    func load(force: Bool) async throws -> BriefingIndexFetchResult? {
        let requestedMode: LoadMode = force ? .forced : .conditional
        if let task = tasks.task(for: .index) {
            if requestedMode == .forced, loadMode == .conditional {
                cancelIndexLoad()
            } else {
                await task.value
                return nil
            }
        }

        loadMode = requestedMode
        var result: BriefingIndexFetchResult?
        var loadError: Error?
        let startedAt = Date()
        let priorETag = etag
        let task = tasks.runReplacing(.index) { [weak self] token in
            guard let self else { return }
            do {
                let fetched = try await service.fetchIndex(ifNoneMatch: force ? nil : etag)
                guard tasks.isCurrent(token), !Task.isCancelled else { return }
                result = fetched
                updateETag(from: fetched)
                logIndexLoad(fetched, token: token, priorETag: priorETag, startedAt: startedAt)
            } catch {
                guard tasks.isCurrent(token) else { return }
                loadError = error
            }
            if tasks.isCurrent(token) {
                loadMode = nil
            }
        }
        await task.value

        if let loadError {
            throw loadError
        }
        return result
    }

    func refresh(
        prepare: @escaping @MainActor () async -> Void,
        onIndexResult: @escaping @MainActor (BriefingIndexFetchResult) -> Void
    ) async {
        if let task = tasks.task(for: .manualRefresh) {
            await task.value
            return
        }

        let task = tasks.runReplacing(.manualRefresh) { [weak self] token in
            await self?.performRefresh(
                token: token,
                prepare: prepare,
                onIndexResult: onIndexResult
            )
        }
        await task.value
    }

    private func performRefresh(
        token: TaskBag<TaskKey>.Token,
        prepare: @escaping @MainActor () async -> Void,
        onIndexResult: @escaping @MainActor (BriefingIndexFetchResult) -> Void
    ) async {
        refreshPhase = .requesting
        tasks.cancel(.refreshPoll)
        tasks.cancel(.activation)
        cancelIndexLoad()
        await prepare()
        guard tasks.isCurrent(token), !Task.isCancelled else { return }

        let startedAt = Date()
        do {
            let response = try await service.requestRefresh()
            guard tasks.isCurrent(token), !Task.isCancelled else { return }
            briefingIndexLogger.info(
                "Refresh accepted | duration_ms=\(Int(Date().timeIntervalSince(startedAt) * 1_000), privacy: .public) baseline_version=\(response.version, privacy: .public)"
            )
            guard isActive else {
                refreshPhase = .idle
                return
            }
            refreshPhase = .waitingForVersion
            startRefreshPolling(
                baselineVersion: response.version,
                onIndexResult: onIndexResult
            )
        } catch where isNetworkCancellation(error) {
            briefingIndexLogger.info("Manual Briefing refresh cancelled")
            if tasks.isCurrent(token) {
                refreshPhase = .idle
            }
        } catch {
            guard tasks.isCurrent(token) else { return }
            briefingIndexLogger.error(
                "Manual Briefing refresh failed | error=\(error.localizedDescription, privacy: .private)"
            )
            refreshPhase = .failed(error.localizedDescription)
        }
    }

    private func startRefreshPolling(
        baselineVersion: Int,
        onIndexResult: @escaping @MainActor (BriefingIndexFetchResult) -> Void
    ) {
        guard isActive else {
            refreshPhase = .idle
            return
        }

        tasks.runReplacing(.refreshPoll) { [weak self] token in
            guard let self else { return }
            var pollCount = 0
            for delay in refreshPollDelays {
                do {
                    let jitter = UInt64.random(in: 0...min(delay / 10, 250_000_000))
                    try await Task.sleep(nanoseconds: delay + jitter)
                    pollCount += 1
                    let result = try await service.fetchIndex(ifNoneMatch: etag)
                    guard tasks.isCurrent(token), !Task.isCancelled else { return }
                    guard case .value(let response, _) = result else {
                        continue
                    }
                    updateETag(from: result)
                    onIndexResult(result)
                    guard response.version != baselineVersion else { continue }
                    briefingIndexLogger.info(
                        "Refresh poll completed | polls=\(pollCount, privacy: .public) baseline_version=\(baselineVersion, privacy: .public) new_version=\(response.version, privacy: .public)"
                    )
                    refreshPhase = .idle
                    return
                } catch where isNetworkCancellation(error) {
                    guard tasks.isCurrent(token) else { return }
                    briefingIndexLogger.info(
                        "Refresh poll cancelled | polls=\(pollCount, privacy: .public)"
                    )
                    if refreshPhase == .waitingForVersion {
                        refreshPhase = .idle
                    }
                    return
                } catch {
                    guard tasks.isCurrent(token), !Task.isCancelled else { return }
                    briefingIndexLogger.error(
                        "Refresh poll failed | polls=\(pollCount, privacy: .public) error=\(error.localizedDescription, privacy: .private)"
                    )
                    refreshPhase = .failed(error.localizedDescription)
                    return
                }
            }
            guard tasks.isCurrent(token), !Task.isCancelled else { return }
            if refreshPhase == .waitingForVersion {
                briefingIndexLogger.info(
                    "Refresh poll deadline reached | polls=\(pollCount, privacy: .public) baseline_version=\(baselineVersion, privacy: .public)"
                )
                refreshPhase = .idle
            }
        }
    }

    private func updateETag(from result: BriefingIndexFetchResult) {
        guard case .value(_, let responseETag) = result,
              let responseETag else { return }
        etag = responseETag
    }

    private func logIndexLoad(
        _ result: BriefingIndexFetchResult,
        token: TaskBag<TaskKey>.Token,
        priorETag: String?,
        startedAt: Date
    ) {
        let duration = Int(Date().timeIntervalSince(startedAt) * 1_000)
        switch result {
        case .notModified:
            briefingIndexLogger.info(
                "Index loaded | generation=\(token.generation, privacy: .public) status=304 duration_ms=\(duration, privacy: .public)"
            )
        case .value(let response, let responseETag):
            briefingIndexLogger.info(
                "Index loaded | generation=\(token.generation, privacy: .public) status=200 duration_ms=\(duration, privacy: .public) new_version=\(response.version, privacy: .public) etag_changed=\(responseETag != nil && responseETag != priorETag, privacy: .public)"
            )
        }
    }
}
