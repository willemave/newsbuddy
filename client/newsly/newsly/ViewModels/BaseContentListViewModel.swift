//
//  BaseContentListViewModel.swift
//  newsly
//
//  Created by Assistant on 3/16/26.
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "BaseContentList")

enum LoadingState: Equatable {
    case idle
    case initialLoading
    case loadingMore
    case error(Error)
    case endOfFeed
}

struct Pagination {
    var nextCursor: String?
    var hasMore: Bool
    var isLoading: Bool
}

extension LoadingState {
    static func == (lhs: LoadingState, rhs: LoadingState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle),
             (.initialLoading, .initialLoading),
             (.loadingMore, .loadingMore),
             (.endOfFeed, .endOfFeed):
            return true
        case (.error, .error):
            return true
        default:
            return false
        }
    }
}

@MainActor
class BaseContentListViewModel: ObservableObject {
    @Published private(set) var items: [ContentSummary] = []
    @Published private(set) var state: LoadingState = .idle

    let refreshTrigger = PassthroughSubject<Void, Never>()
    let loadMoreTrigger = PassthroughSubject<Void, Never>()
    let clearReadTrigger = PassthroughSubject<Void, Never>()

    private let repository: ContentRepositoryType
    private var contentTypes: [ContentType]
    private var readFilter: ReadFilter

    private var pagination = Pagination(nextCursor: nil, hasMore: true, isLoading: false)
    private var cancellables = Set<AnyCancellable>()

    init(
        repository: ContentRepositoryType,
        contentTypes: [ContentType],
        readFilter: ReadFilter = .unread
    ) {
        self.repository = repository
        self.contentTypes = contentTypes
        self.readFilter = readFilter
        bind()
    }

    func startInitialLoad() {
        guard !pagination.isLoading else { return }
        pagination = Pagination(nextCursor: nil, hasMore: true, isLoading: true)
        items.removeAll()
        state = .initialLoading
        requestPage(cursor: nil, replaceItems: false)
    }

    func refreshInBackground() {
        guard !pagination.isLoading else { return }
        pagination.isLoading = true
        requestPage(cursor: nil, replaceItems: true)
    }

    func loadNextPage() {
        guard !pagination.isLoading, pagination.hasMore else { return }
        state = .loadingMore
        requestPage(cursor: pagination.nextCursor, replaceItems: false)
    }

    func updateReadFilter(_ newValue: ReadFilter) {
        guard newValue != readFilter else { return }
        readFilter = newValue
        startInitialLoad()
    }

    func markItemLocallyRead(id: Int) {
        guard let index = items.firstIndex(where: { $0.id == id }) else {
            logger.warning("[BaseContentList] markItemLocallyRead: item not found | id=\(id) itemCount=\(self.items.count)")
            return
        }
        let oldIsRead = items[index].isRead
        items[index] = items[index].updating(isRead: true)
        logger.info("[BaseContentList] markItemLocallyRead | id=\(id) wasRead=\(oldIsRead) index=\(index)")
    }

    @discardableResult
    func markItemsLocallyRead(ids: [Int]) -> [Int] {
        markItemsLocallyRead(ids: ids, removeReadItems: false).map(\.id)
    }

    @discardableResult
    func markItemsLocallyRead(ids: [Int], removeReadItems: Bool) -> [ContentSummary] {
        guard !ids.isEmpty else { return [] }

        let targetIds = Set(ids)
        var nextItems = items
        var markedItems: [ContentSummary] = []

        for index in nextItems.indices {
            let item = nextItems[index]
            guard targetIds.contains(item.id), !item.isRead else { continue }

            let updatedItem = item.updating(isRead: true)
            nextItems[index] = updatedItem
            markedItems.append(updatedItem)
        }

        guard !markedItems.isEmpty else {
            logger.debug("[BaseContentList] markItemsLocallyRead: no unread matches")
            return []
        }

        if removeReadItems {
            nextItems.removeAll { $0.isRead }
        }

        items = nextItems
        let markedIds = markedItems.map(\.id)
        logger.info("[BaseContentList] markItemsLocallyRead | ids=\(markedIds, privacy: .public) count=\(markedIds.count)")
        return markedItems
    }

    func dropReadItems() {
        items.removeAll { $0.isRead }
    }

    func currentReadFilter() -> ReadFilter {
        readFilter
    }

    func currentItems() -> [ContentSummary] {
        items
    }

    func updateItem(id: Int, transform: (ContentSummary) -> ContentSummary) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        items[index] = transform(items[index])
    }

    func replaceItems(_ newItems: [ContentSummary]) {
        items = newItems
    }

    // MARK: - Private
    private func bind() {
        refreshTrigger
            .sink { [weak self] in
                self?.startInitialLoad()
            }
            .store(in: &cancellables)

        loadMoreTrigger
            .sink { [weak self] in
                self?.loadNextPage()
            }
            .store(in: &cancellables)

        clearReadTrigger
            .sink { [weak self] in
                self?.dropReadItems()
            }
            .store(in: &cancellables)
    }

    private func requestPage(cursor: String?, replaceItems: Bool) {
        repository
            .loadPage(
                contentTypes: contentTypes,
                readFilter: readFilter,
                cursor: cursor,
                limit: nil
            )
            .receive(on: DispatchQueue.main)
            .sink { [weak self] completion in
                guard let self else { return }
                pagination.isLoading = false
                switch completion {
                case .failure(let error):
                    state = .error(error)
                case .finished:
                    if !pagination.hasMore {
                        state = .endOfFeed
                    } else {
                        state = .idle
                    }
                }
            } receiveValue: { [weak self] response in
                guard let self else { return }
                pagination.hasMore = response.hasMore
                pagination.nextCursor = response.nextCursor
                if replaceItems {
                    items = response.contents
                } else {
                    items.append(contentsOf: response.contents)
                }
            }
            .store(in: &cancellables)
    }
}
