//
//  ScraperSettingsViewModel.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ScraperSettings")

protocol ScraperSettingsServicing: AnyObject {
    func listConfigs(types: [String]?, includeStats: Bool) async throws -> [ScraperConfig]
    func createConfig(
        scraperType: String,
        displayName: String?,
        feedURL: String,
        limit: Int?,
        isActive: Bool
    ) async throws -> ScraperConfig
    func updateConfig(
        configId: Int,
        displayName: String?,
        feedURL: String?,
        limit: Int?,
        isActive: Bool?
    ) async throws -> ScraperConfig
    func deleteConfig(configId: Int) async throws
}

extension ScraperConfigService: ScraperSettingsServicing {}

@MainActor
@Observable
final class ScraperSettingsViewModel {
    var configs: [ScraperConfig] = []
    var isLoading: Bool = false
    var errorMessage: String?

    @ObservationIgnored
    private let filterTypes: [String]?
    @ObservationIgnored
    private let service: any ScraperSettingsServicing
    @ObservationIgnored
    private var activeLoad: ActiveConfigLoad?
    @ObservationIgnored
    private var mutationLog = AuthoritativeMutationLog<Int, ScraperConfig>()

    init(filterTypes: [String]? = nil, service: any ScraperSettingsServicing) {
        self.filterTypes = filterTypes
        self.service = service
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
        let requestStartRevision = mutationLog.currentRevision
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
            let loadedConfigs = try await service.listConfigs(
                types: filterTypes,
                includeStats: includeStats
            )
            guard !Task.isCancelled else { return false }
            configs = reconcileLoadedConfigs(
                loadedConfigs,
                requestStartRevision: requestStartRevision
            )
            return true
        } catch where ClientFailure.classify(error) == .cancelled {
            return false
        } catch {
            logger.error("Failed to load scraper configs: \(error.localizedDescription, privacy: .public)")
            errorMessage = "Newsbuddy couldn't load your sources. Please try again."
            return false
        }
    }

    @discardableResult
    func addConfig(scraperType: String, displayName: String?, feedURL: String, limit: Int? = nil) async -> Bool {
        errorMessage = nil
        do {
            let newConfig = try await service.createConfig(
                scraperType: scraperType,
                displayName: displayName,
                feedURL: feedURL,
                limit: limit,
                isActive: true
            )
            upsert(newConfig)
            return true
        } catch {
            logger.error("Failed to add scraper config: \(error.localizedDescription, privacy: .public)")
            errorMessage = "Newsbuddy couldn't add this source. Check the URL and try again."
            return false
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
            upsert(updated)
        } catch {
            logger.error("Failed to update scraper config: \(error.localizedDescription, privacy: .public)")
            errorMessage = "Newsbuddy couldn't update this source. Please try again."
        }
    }

    func deleteConfig(_ config: ScraperConfig) async {
        errorMessage = nil
        do {
            try await service.deleteConfig(configId: config.id)
            mutationLog.recordRemoval(id: config.id)
            configs.removeAll { $0.id == config.id }
        } catch {
            logger.error("Failed to delete scraper config: \(error.localizedDescription, privacy: .public)")
            errorMessage = "Newsbuddy couldn't remove this source. Please try again."
        }
    }

    private func upsert(_ config: ScraperConfig) {
        mutationLog.recordUpsert(config, id: config.id)
        if let index = configs.firstIndex(where: { $0.id == config.id }) {
            configs[index] = config
        } else {
            configs.insert(config, at: 0)
        }
    }

    private func reconcileLoadedConfigs(
        _ loadedConfigs: [ScraperConfig],
        requestStartRevision: Int
    ) -> [ScraperConfig] {
        mutationLog.reconcile(
            authoritative: loadedConfigs,
            requestStartRevision: requestStartRevision,
            id: \.id
        )
    }
}

private struct ActiveConfigLoad {
    let id: UUID
    let task: Task<Void, Never>
}
