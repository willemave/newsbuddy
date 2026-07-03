import Foundation

@MainActor
final class BriefingDigViewModel: ObservableObject {
    enum State {
        case idle
        case searching
        case summarizing(results: [APIBriefingDigSearchResult])
        case loaded(results: [APIBriefingDigSearchResult], summary: String)
        case error(String)
    }

    @Published private(set) var fragment: String?
    @Published private(set) var state: State = .idle

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
    private var cache: [String: State] = [:]
    private var currentTask: Task<Void, Never>?

    init(service: BriefingServicing) {
        self.service = service
    }

    func dig(fragment rawFragment: String, passageContext: String) {
        let normalized = rawFragment.trimmingCharacters(in: .whitespacesAndNewlines)
        guard normalized.count >= 3 else { return }
        fragment = normalized
        if let cached = cache[normalized.lowercased()] {
            state = cached
            return
        }

        currentTask?.cancel()
        currentTask = Task { [weak self] in
            guard let self else { return }
            do {
                self.state = .searching
                let search = try await service.digSearch(fragment: normalized)
                self.state = .summarizing(results: search.results)
                let summary = try await service.digSummarize(
                    fragment: normalized,
                    passageContext: passageContext,
                    results: search.results
                )
                let loaded = State.loaded(results: search.results, summary: summary.summary)
                self.cache[normalized.lowercased()] = loaded
                self.state = loaded
            } catch {
                if !isNetworkCancellation(error) {
                    self.state = .error("Couldn't dig into that just now. Try again.")
                }
            }
        }
    }

    func clear() {
        currentTask?.cancel()
        fragment = nil
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
