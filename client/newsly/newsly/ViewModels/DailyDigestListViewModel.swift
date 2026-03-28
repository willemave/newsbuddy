//
//  DailyDigestListViewModel.swift
//  newsly
//

import Combine
import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "DailyDigestList")

struct DailyDigestBulletKey: Hashable {
    let digestId: Int
    let bulletIndex: Int
}

@MainActor
final class DailyDigestListViewModel: ObservableObject {
    @Published private(set) var items: [DailyNewsDigest] = []
    @Published private(set) var state: LoadingState = .idle
    @Published private(set) var digDeeperLoadingKeys: Set<DailyDigestBulletKey> = []
    @Published private(set) var digDeeperErrors: [DailyDigestBulletKey: String] = [:]

    let refreshTrigger = PassthroughSubject<Void, Never>()
    let loadMoreTrigger = PassthroughSubject<Void, Never>()

    private let repository: DailyNewsDigestRepositoryType
    private let unreadCountService: UnreadCountService
    private var readFilter: ReadFilter
    private var pagination = Pagination(nextCursor: nil, hasMore: true, isLoading: false)
    private var cancellables = Set<AnyCancellable>()

    init(
        repository: DailyNewsDigestRepositoryType,
        unreadCountService: UnreadCountService,
        readFilter: ReadFilter = .unread
    ) {
        self.repository = repository
        self.unreadCountService = unreadCountService
        self.readFilter = readFilter
        bind()
    }

    func startInitialLoad() {
        guard !pagination.isLoading else { return }
        pagination = Pagination(nextCursor: nil, hasMore: true, isLoading: true)
        items.removeAll()
        state = .initialLoading
        requestPage(cursor: nil)
    }

    func loadNextPage() {
        guard !pagination.isLoading, pagination.hasMore else { return }
        state = .loadingMore
        requestPage(cursor: pagination.nextCursor)
    }

    func currentItems() -> [DailyNewsDigest] {
        items
    }

    func markDigestRead(id: Int) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        guard items[index].isRead == false else { return }

        let originalDigest = items[index]
        let shouldRemoveFromUnreadList = readFilter == .unread
        if shouldRemoveFromUnreadList {
            items.remove(at: index)
            if pagination.hasMore, !pagination.isLoading {
                requestPage(cursor: pagination.nextCursor)
            }
        } else {
            items[index].isRead = true
        }
        unreadCountService.decrementDailyDigestCount()
        repository
            .markRead(id: id)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] completion in
                guard let self else { return }
                if case .failure(let error) = completion {
                    logger.error("[DailyDigestList] markRead failed | id=\(id) error=\(error.localizedDescription)")
                    self.unreadCountService.incrementDailyDigestCount()
                    if shouldRemoveFromUnreadList {
                        let restoreIndex = min(index, self.items.count)
                        self.items.insert(originalDigest, at: restoreIndex)
                    } else if let restoreIndex = self.items.firstIndex(where: { $0.id == id }) {
                        self.items[restoreIndex].isRead = false
                    }
                }
            } receiveValue: { }
            .store(in: &cancellables)
    }

    func markDigestUnread(id: Int) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        guard items[index].isRead else { return }

        items[index].isRead = false
        unreadCountService.incrementDailyDigestCount()
        repository
            .markUnread(id: id)
            .receive(on: DispatchQueue.main)
            .sink { completion in
                if case .failure(let error) = completion {
                    logger.error("[DailyDigestList] markUnread failed | id=\(id) error=\(error.localizedDescription)")
                }
            } receiveValue: { }
            .store(in: &cancellables)
    }

    func isStartingDigDeeperChat(digestId: Int, bulletIndex: Int) -> Bool {
        digDeeperLoadingKeys.contains(DailyDigestBulletKey(digestId: digestId, bulletIndex: bulletIndex))
    }

    func digDeeperError(digestId: Int, bulletIndex: Int) -> String? {
        digDeeperErrors[DailyDigestBulletKey(digestId: digestId, bulletIndex: bulletIndex)]
    }

    func clearDigDeeperError(digestId: Int, bulletIndex: Int) {
        digDeeperErrors[DailyDigestBulletKey(digestId: digestId, bulletIndex: bulletIndex)] = nil
    }

    func startBulletDigDeeperChat(
        digestId: Int,
        bulletIndex: Int
    ) async throws -> ChatSessionRoute {
        let key = DailyDigestBulletKey(digestId: digestId, bulletIndex: bulletIndex)
        digDeeperErrors[key] = nil
        digDeeperLoadingKeys.insert(key)
        defer { digDeeperLoadingKeys.remove(key) }

        do {
            let response = try await repository.startBulletDigDeeperChat(
                digestId: digestId,
                bulletIndex: bulletIndex
            )
            return ChatSessionRoute(sessionId: response.session.id)
        } catch {
            digDeeperErrors[key] = error.localizedDescription
            throw error
        }
    }

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
    }

    private func requestPage(cursor: String?) {
        pagination.isLoading = true
        repository
            .loadPage(
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
                    state = pagination.hasMore ? .idle : .endOfFeed
                }
            } receiveValue: { [weak self] response in
                guard let self else { return }
                pagination.hasMore = response.hasMore
                pagination.nextCursor = response.nextCursor
                items.append(contentsOf: response.digests)
            }
            .store(in: &cancellables)
    }
}
