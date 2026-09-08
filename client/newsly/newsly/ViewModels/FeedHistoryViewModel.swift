import Foundation
import Observation

protocol FeedHistoryServicing {
    func feedHistory(configId: Int, status: String, offset: Int) async throws -> APIFeedHistoryResponse
}

extension ScraperConfigService: FeedHistoryServicing {}

@MainActor
@Observable
final class FeedHistoryViewModel {
    var items: [APIFeedHistoryItem] = []
    var activeItems: [APIFeedHistoryItem] = []
    var isLoading = false
    var errorMessage: String?
    var nextOffset: Int?
    private var revision = 0
    private var loadedStatus = "completed"
    private let configId: Int
    private let service: any FeedHistoryServicing

    init(configId: Int, service: any FeedHistoryServicing = ScraperConfigService.shared) {
        self.configId = configId
        self.service = service
    }

    func refresh(status: String) async {
        revision += 1
        let requestRevision = revision
        if loadedStatus != status { items = []; nextOffset = nil }
        loadedStatus = status
        isLoading = true
        errorMessage = nil
        defer { if revision == requestRevision { isLoading = false } }
        do {
            async let history = service.feedHistory(configId: configId, status: status, offset: 0)
            async let active = service.feedHistory(configId: configId, status: "active", offset: 0)
            let (page, activePage) = try await (history, active)
            guard revision == requestRevision, !Task.isCancelled else { return }
            items = page.items
            activeItems = activePage.items
            nextOffset = page.nextOffset
        } catch {
            guard revision == requestRevision, ClientFailure.classify(error) != .cancelled else { return }
            errorMessage = "Couldn't load this feed's history. Please try again."
        }
    }

    func refreshActivity() async {
        let requestRevision = revision
        do {
            let page = try await service.feedHistory(configId: configId, status: "active", offset: 0)
            guard revision == requestRevision, !Task.isCancelled else { return }
            activeItems = page.items
            if loadedStatus == "active", !isLoading, items.count <= 30 {
                items = page.items
                nextOffset = page.nextOffset
            }
        } catch {
            guard revision == requestRevision, ClientFailure.classify(error) != .cancelled else { return }
            errorMessage = "Couldn't update current work. Pull to refresh."
        }
    }

    func loadMore() async {
        guard !isLoading, let offset = nextOffset else { return }
        let requestRevision = revision
        isLoading = true
        errorMessage = nil
        defer { if revision == requestRevision { isLoading = false } }
        do {
            let page = try await service.feedHistory(configId: configId, status: loadedStatus, offset: offset)
            guard revision == requestRevision, !Task.isCancelled else { return }
            let existing = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { !existing.contains($0.id) })
            nextOffset = page.nextOffset
        } catch {
            guard revision == requestRevision, ClientFailure.classify(error) != .cancelled else { return }
            errorMessage = "Couldn't load more items. Please try again."
        }
    }
}
