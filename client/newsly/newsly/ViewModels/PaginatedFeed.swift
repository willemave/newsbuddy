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

enum PageReadResult: Equatable {
    case succeeded
    case failed(String)
    case cancelled
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
        loadPage: @escaping (_ cursor: String?) async throws -> Page<Item>
    ) {
        self.init(
            items: items,
            phase: phase,
            nextCursor: nextCursor,
            hasMore: hasMore,
            loadPageWithGeneration: { cursor, _ in try await loadPage(cursor) }
        )
    }

    init(
        items: [Item] = [],
        phase: LoadPhase = .idle,
        nextCursor: String? = nil,
        hasMore: Bool = true,
        loadPageWithGeneration: @escaping (_ cursor: String?, _ generation: Int) async throws -> Page<Item>
    ) {
        self.items = items
        self.phase = phase
        self.nextCursor = nextCursor
        self.hasMore = hasMore
        self.loadPage = loadPageWithGeneration
    }

    func loadInitial() async {
        await requestPage(
            cursor: nil,
            loadingPhase: .initialLoading,
            mode: .replace(clearExistingItems: true)
        )
    }

    @discardableResult
    func refresh() async -> PageReadResult {
        await requestPage(
            cursor: nil,
            loadingPhase: items.isEmpty ? .initialLoading : .loaded,
            mode: .replace(clearExistingItems: false)
        )
    }

    /// Refresh every loaded page so background observation preserves scroll
    /// depth and can update processing items beyond the first page.
    @discardableResult
    func refreshLoadedPages(
        isOrderedBefore: @escaping (Item, Item) -> Bool
    ) async -> PageReadResult {
        guard let boundary = items.last else { return await refresh() }
        return await requestPage(
            cursor: nil,
            loadingPhase: items.isEmpty ? .initialLoading : .loaded,
            mode: .replace(clearExistingItems: false),
            precedesLoadedBoundary: { isOrderedBefore($0, boundary) }
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
        case replace(clearExistingItems: Bool)
        case append
    }

    @discardableResult
    private func requestPage(
        cursor: String?,
        loadingPhase: LoadPhase,
        mode: RequestMode,
        precedesLoadedBoundary: ((Item) -> Bool)? = nil
    ) async -> PageReadResult {
        requestGeneration += 1
        let generation = requestGeneration
        requestTask?.cancel()
        let previousPhase = phase
        phaseBeforeRequest = previousPhase
        phase = loadingPhase
        isRequestInFlight = true

        if case .replace(let clearExistingItems) = mode, clearExistingItems {
            items.removeAll()
            nextCursor = nil
            hasMore = true
        }

        let loader = loadPage
        let task = Task {
            var page = try await loader(cursor, generation)
            var visitedCursors = Set<String>()
            var existingIDs = Set(page.items.map(\.id))
            while let precedesLoadedBoundary, page.hasMore,
                  page.items.last.map(precedesLoadedBoundary) ?? true {
                try Task.checkCancellation()
                guard let next = page.nextCursor, visitedCursors.insert(next).inserted else {
                    throw URLError(.cannotParseResponse)
                }
                let additional = try await loader(next, generation)
                page = Page(
                    items: page.items + additional.items.filter { existingIDs.insert($0.id).inserted },
                    nextCursor: additional.nextCursor,
                    hasMore: additional.hasMore
                )
            }
            return page
        }
        requestTask = task

        do {
            let page = try await withTaskCancellationHandler {
                try await task.value
            } onCancel: {
                task.cancel()
            }
            try Task.checkCancellation()
            guard generation == requestGeneration else { return .cancelled }

            apply(page: page, mode: mode)
            phase = items.isEmpty ? .empty : .loaded
            requestTask = nil
            phaseBeforeRequest = nil
            isRequestInFlight = false
            return .succeeded
        } catch where ClientFailure.classify(error) == .cancelled {
            guard generation == requestGeneration else { return .cancelled }
            phase = previousPhase
            requestTask = nil
            phaseBeforeRequest = nil
            isRequestInFlight = false
            return .cancelled
        } catch {
            guard generation == requestGeneration else { return .cancelled }
            phase = .error(error.localizedDescription)
            requestTask = nil
            phaseBeforeRequest = nil
            isRequestInFlight = false
            return .failed(error.localizedDescription)
        }
    }

    private func apply(page: Page<Item>, mode: RequestMode) {
        nextCursor = page.nextCursor
        hasMore = page.hasMore

        switch mode {
        case .replace:
            items = page.items
        case .append:
            let existingIDs = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { !existingIDs.contains($0.id) })
        }
    }
}
