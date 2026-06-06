//
//  ScraperSettingsViewModel.swift
//  newsly
//

import Foundation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ScraperSettings")

@MainActor
class ScraperSettingsViewModel: ObservableObject {
    @Published var configs: [ScraperConfig] = []
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    private let filterTypes: [String]?
    private let service = ScraperConfigService.shared
    private var activeLoad: ActiveConfigLoad?

    init(filterTypes: [String]? = nil) {
        self.filterTypes = filterTypes
    }

    func loadConfigs(includeStats: Bool = true, showLoading: Bool = true) async {
        await enqueueLoad {
            _ = await self.performLoadConfigs(includeStats: includeStats, showLoading: showLoading)
        }
    }

    func loadConfigsWithDeferredStats() async {
        await enqueueLoad {
            let loadedFastConfig = await self.performLoadConfigs(includeStats: false, showLoading: true)
            guard loadedFastConfig, !Task.isCancelled else { return }
            _ = await self.performLoadConfigs(includeStats: true, showLoading: false)
        }
    }

    private func enqueueLoad(_ operation: @escaping @MainActor () async -> Void) async {
        let previousTask = activeLoad?.task
        let loadId = UUID()
        let task = Task { @MainActor in
            await previousTask?.value
            await operation()
        }
        activeLoad = ActiveConfigLoad(id: loadId, task: task)
        await task.value
        if activeLoad?.id == loadId {
            activeLoad = nil
        }
    }

    private func performLoadConfigs(includeStats: Bool, showLoading: Bool) async -> Bool {
        if showLoading {
            isLoading = true
        }
        errorMessage = nil
        defer {
            if showLoading {
                isLoading = false
            }
        }

        do {
            configs = try await service.listConfigs(
                types: filterTypes,
                includeStats: includeStats
            )
            return true
        } catch where isNetworkCancellation(error) {
            return false
        } catch {
            logger.error("Failed to load scraper configs: \(error.localizedDescription, privacy: .public)")
            errorMessage = error.localizedDescription
            return false
        }
    }

    func addConfig(scraperType: String, displayName: String?, feedURL: String, limit: Int? = nil) async {
        errorMessage = nil
        do {
            let newConfig = try await service.createConfig(
                scraperType: scraperType,
                displayName: displayName,
                feedURL: feedURL,
                limit: limit,
                isActive: true
            )
            configs.insert(newConfig, at: 0)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func updateConfig(
        _ config: ScraperConfig,
        isActive: Bool? = nil,
        displayName: String? = nil,
        feedURL: String? = nil,
        limit: Int? = nil
    ) async {
        errorMessage = nil
        do {
            let updated = try await service.updateConfig(
                configId: config.id,
                displayName: displayName,
                feedURL: feedURL,
                limit: limit,
                isActive: isActive
            )
            if let index = configs.firstIndex(where: { $0.id == updated.id }) {
                configs[index] = updated
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func deleteConfig(_ config: ScraperConfig) async {
        errorMessage = nil
        do {
            try await service.deleteConfig(configId: config.id)
            configs.removeAll { $0.id == config.id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct ActiveConfigLoad {
    let id: UUID
    let task: Task<Void, Never>
}
