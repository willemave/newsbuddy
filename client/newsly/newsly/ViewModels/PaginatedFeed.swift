//
//  PaginatedFeed.swift
//  newsly
//

import Foundation
import Observation

enum LoadPhase: Equatable {
    case idle
    case initialLoading
    case empty
    case loaded
    case loadingMore
    case error(String)
}

struct Page<Item: Identifiable & Sendable>: Sendable where Item.ID: Sendable {
    let items: [Item]
    let nextCursor: String?
    let hasMore: Bool

    init(items: [Item], nextCursor: String?, hasMore: Bool) {
        self.items = items
        self.nextCursor = nextCursor
        self.hasMore = hasMore
    }
}

@MainActor
@Observable
final class PaginatedFeed<Item: Identifiable & Sendable> where Item.ID: Hashable & Sendable {
    private(set) var items: [Item]
    private(set) var phase: LoadPhase
    private(set) var nextCursor: String?
    private(set) var hasMore: Bool
    private(set) var isRequestInFlight = false

    @ObservationIgnored
    private let loadPage: (_ cursor: String?, _ generation: Int) async throws -> Page<Item>

    @ObservationIgnored
    private let mergeReplacement: (_ current: [Item], _ incoming: [Item]) -> [Item]

    @ObservationIgnored
    private var requestGeneration = 0

    @ObservationIgnored
    private var requestTask: Task<Page<Item>, Error>?

    @ObservationIgnored
    private var phaseBeforeRequest: LoadPhase?

    convenience init(
        items: [Item] = [],
        phase: LoadPhase = .idle,
        nextCursor: String? = nil,
        hasMore: Bool = true,
        loadPage: @escaping (_ cursor: String?) async throws -> Page<Item>,
        mergeReplacement: @escaping (_ current: [Item], _ incoming: [Item]) -> [Item] = { _, incoming in incoming }
    ) {
        self.init(
            items: items,
            phase: phase,
            nextCursor: nextCursor,
            hasMore: hasMore,
            loadPageWithGeneration: { cursor, _ in try await loadPage(cursor) },
            mergeReplacement: mergeReplacement
        )
    }

    init(
        items: [Item] = [],
        phase: LoadPhase = .idle,
        nextCursor: String? = nil,
        hasMore: Bool = true,
        loadPageWithGeneration: @escaping (_ cursor: String?, _ generation: Int) async throws -> Page<Item>,
        mergeReplacement: @escaping (_ current: [Item], _ incoming: [Item]) -> [Item] = { _, incoming in incoming }
    ) {
        self.items = items
        self.phase = phase
        self.nextCursor = nextCursor
        self.hasMore = hasMore
        self.loadPage = loadPageWithGeneration
        self.mergeReplacement = mergeReplacement
    }

    func loadInitial() async {
        await requestPage(
            cursor: nil,
            loadingPhase: .initialLoading,
            mode: .replace(clearExistingItems: true, mergeWithCurrentItems: false)
        )
    }

    func refresh() async {
        await requestPage(
            cursor: nil,
            loadingPhase: items.isEmpty ? .initialLoading : .loaded,
            mode: .replace(clearExistingItems: false, mergeWithCurrentItems: false)
        )
    }

    func refreshInBackground() async {
        guard requestTask == nil else { return }
        await requestPage(
            cursor: nil,
            loadingPhase: phase,
            mode: .replace(clearExistingItems: false, mergeWithCurrentItems: true)
        )
    }

    func loadNextPage() async {
        guard requestTask == nil, hasMore, let nextCursor else { return }
        await requestPage(
            cursor: nextCursor,
            loadingPhase: .loadingMore,
            mode: .append
        )
    }

    func replaceItems(_ newItems: [Item]) {
        items = newItems
        switch phase {
        case .empty, .loaded:
            phase = newItems.isEmpty ? .empty : .loaded
        case .idle, .initialLoading, .loadingMore, .error:
            break
        }
    }

    func reset() {
        requestGeneration += 1
        requestTask?.cancel()
        requestTask = nil
        phaseBeforeRequest = nil
        isRequestInFlight = false
        items.removeAll()
        phase = .idle
        nextCursor = nil
        hasMore = true
    }

    /// Stops an obsolete automatic read without discarding readable state.
    ///
    /// Incrementing the generation also fences a loader that ignores task
    /// cancellation, so a replacement activation can start immediately.
    func cancelRequestRetainingState() {
        guard requestTask != nil else { return }
        requestGeneration += 1
        requestTask?.cancel()
        requestTask = nil
        isRequestInFlight = false
        phase = phaseBeforeRequest ?? (items.isEmpty ? .idle : .loaded)
        phaseBeforeRequest = nil
    }

    func isCurrentRequest(_ generation: Int) -> Bool {
        generation == requestGeneration
    }

    private enum RequestMode {
        case replace(clearExistingItems: Bool, mergeWithCurrentItems: Bool)
        case append
    }

    private func requestPage(
        cursor: String?,
        loadingPhase: LoadPhase,
        mode: RequestMode
    ) async {
        requestGeneration += 1
        let generation = requestGeneration
        requestTask?.cancel()
        let previousPhase = phase
        phaseBeforeRequest = previousPhase
        phase = loadingPhase
        isRequestInFlight = true

        if case .replace(let clearExistingItems, _) = mode, clearExistingItems {
            items.removeAll()
            nextCursor = nil
            hasMore = true
        }

        let loader = loadPage
        let task = Task {
            try await loader(cursor, generation)
        }
        requestTask = task

        do {
            let page = try await withTaskCancellationHandler {
                try await task.value
            } onCancel: {
                task.cancel()
            }
            guard generation == requestGeneration else { return }

            apply(page: page, mode: mode)
            phase = items.isEmpty ? .empty : .loaded
            requestTask = nil
            phaseBeforeRequest = nil
            isRequestInFlight = false
        } catch where ClientFailure.classify(error) == .cancelled {
            guard generation == requestGeneration else { return }
            phase = previousPhase
            requestTask = nil
            phaseBeforeRequest = nil
            isRequestInFlight = false
        } catch {
            guard generation == requestGeneration else { return }
            phase = .error(error.localizedDescription)
            requestTask = nil
            phaseBeforeRequest = nil
            isRequestInFlight = false
        }
    }

    private func apply(page: Page<Item>, mode: RequestMode) {
        nextCursor = page.nextCursor
        hasMore = page.hasMore

        switch mode {
        case .replace(_, let mergeWithCurrentItems):
            items = mergeWithCurrentItems
                ? mergeReplacement(items, page.items)
                : page.items
        case .append:
            let existingIDs = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { !existingIDs.contains($0.id) })
        }
    }
}

extension PaginatedFeed where Item: Equatable {
    static func mergeNewItemsOnTopKeepingExistingOrder(
        current: [Item],
        incoming: [Item]
    ) -> [Item] {
        guard !current.isEmpty else { return incoming }

        let incomingByID = Dictionary(incoming.map { ($0.id, $0) }) { first, _ in first }
        let currentIDs = Set(current.map(\.id))
        let newItems = incoming.filter { !currentIDs.contains($0.id) }
        let keptItems = current.compactMap { item -> Item? in
            guard let updated = incomingByID[item.id] else { return nil }
            return updated == item ? item : updated
        }
        return newItems + keptItems
    }
}
