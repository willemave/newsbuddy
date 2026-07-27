import Foundation
import Observation

@MainActor
@Observable
final class BriefingDigViewModel {
    private enum TaskKey: Hashable {
        case dig
    }

    private struct CacheKey: Hashable {
        let fragment: String
        let passageContext: String
    }

    private struct DigRequest {
        let fragment: String
        let passageContext: String

        var cacheKey: CacheKey {
            CacheKey(fragment: fragment.lowercased(), passageContext: passageContext)
        }
    }

    enum State {
        case idle
        case searching
        case summarizing(results: [APIBriefingDigSearchResult])
        case loaded(results: [APIBriefingDigSearchResult], summary: String)
        case error(String)
    }

    private(set) var fragment: String?
    private(set) var state: State = .idle

    var isIdle: Bool {
        if case .idle = state { return true }
        return false
    }

    /// Stable per-phase key so views can animate state transitions.
    var stateKey: String {
        switch state {
        case .idle: return "idle"
        case .searching: return "searching"
        case .summarizing: return "summarizing"
        case .loaded: return "loaded"
        case .error: return "error"
        }
    }

    private let service: BriefingServicing
    private var cache: [CacheKey: State] = [:]
    private var currentRequest: DigRequest?
    private let tasks = TaskBag<TaskKey>()

    init(service: BriefingServicing) {
        self.service = service
    }

    deinit {
        tasks.cancelAll()
    }

    func dig(fragment rawFragment: String, passageContext: String) {
        guard let normalized = BriefingDigSelectionPolicy.normalize(rawFragment) else { return }
        let request = DigRequest(
            fragment: normalized,
            passageContext: BriefingDigSelectionPolicy.passageContext(
                passageContext,
                around: normalized
            )
        )
        currentRequest = request
        fragment = request.fragment
        run(request, useCache: true)
    }

    func retry() {
        guard let currentRequest else { return }
        run(currentRequest, useCache: false)
    }

    private func run(_ request: DigRequest, useCache: Bool) {
        if useCache, let cached = cache[request.cacheKey] {
            tasks.cancel(.dig)
            state = cached
            return
        }

        tasks.runReplacing(.dig) { [weak self] token in
            guard let self,
                  self.tasks.isCurrent(token),
                  !Task.isCancelled
            else { return }
            do {
                self.state = .searching
                let search = try await service.digSearch(fragment: request.fragment)
                guard self.tasks.isCurrent(token), !Task.isCancelled else { return }
                self.state = .summarizing(results: search.results)
                let summary = try await service.digSummarize(
                    fragment: request.fragment,
                    passageContext: request.passageContext,
                    results: search.results
                )
                guard self.tasks.isCurrent(token), !Task.isCancelled else { return }
                let loaded = State.loaded(results: search.results, summary: summary.summary)
                self.cache[request.cacheKey] = loaded
                self.state = loaded
            } catch {
                guard self.tasks.isCurrent(token), !Task.isCancelled else { return }
                if !isNetworkCancellation(error) {
                    self.state = .error("Couldn't dig into that just now. Try again.")
                }
            }
        }
    }

    func clear() {
        tasks.cancel(.dig)
        fragment = nil
        currentRequest = nil
        state = .idle
    }

    /// Turn bare "[2]" citations in a summary into tappable digsource:// links.
    static func citationLinkedMarkdown(_ summary: String) -> String {
        summary.replacingOccurrences(
            of: #"\[(\d+)\]"#,
            with: #"[\\[$1\\]](digsource://$1)"#,
            options: .regularExpression
        )
    }
}
