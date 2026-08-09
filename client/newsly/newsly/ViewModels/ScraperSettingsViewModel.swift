//
//  ScraperSettingsViewModel.swift
//  newsly
//

import Foundation
import Observation
import os.log

private let logger = Logger(subsystem: "com.newsly", category: "ScraperSettings")

private enum ScraperConfigMutation {
    case upsert(ScraperConfig)
    case remove
}

private struct VersionedScraperConfigMutation {
    let revision: Int
    let mutation: ScraperConfigMutation
}

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
    private var mutationRevision = 0
    @ObservationIgnored
    private var configMutations: [Int: VersionedScraperConfigMutation] = [:]

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
        let requestStartRevision = mutationRevision
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
        } catch where isNetworkCancellation(error) {
            return false
        } catch {
            logger.error("Failed to load scraper configs: \(error.localizedDescription, privacy: .public)")
            errorMessage = "Newsly couldn't load your sources. Please try again."
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
            errorMessage = "Newsly couldn't add this source. Check the URL and try again."
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
            errorMessage = "Newsly couldn't update this source. Please try again."
        }
    }

    func deleteConfig(_ config: ScraperConfig) async {
        errorMessage = nil
        do {
            try await service.deleteConfig(configId: config.id)
            recordMutation(.remove, for: config.id)
            configs.removeAll { $0.id == config.id }
        } catch {
            logger.error("Failed to delete scraper config: \(error.localizedDescription, privacy: .public)")
            errorMessage = "Newsly couldn't remove this source. Please try again."
        }
    }

    private func upsert(_ config: ScraperConfig) {
        recordMutation(.upsert(config), for: config.id)
        if let index = configs.firstIndex(where: { $0.id == config.id }) {
            configs[index] = config
        } else {
            configs.insert(config, at: 0)
        }
    }

    private func recordMutation(_ mutation: ScraperConfigMutation, for configID: Int) {
        mutationRevision &+= 1
        configMutations[configID] = VersionedScraperConfigMutation(
            revision: mutationRevision,
            mutation: mutation
        )
    }

    private func reconcileLoadedConfigs(
        _ loadedConfigs: [ScraperConfig],
        requestStartRevision: Int
    ) -> [ScraperConfig] {
        var reconciled = loadedConfigs
        let loadedConfigIDs = Set(loadedConfigs.map(\.id))
        let mutations = configMutations.sorted { $0.value.revision < $1.value.revision }

        for (configID, versionedMutation) in mutations {
            switch versionedMutation.mutation {
            case .upsert(let config):
                if versionedMutation.revision <= requestStartRevision,
                   loadedConfigIDs.contains(configID) {
                    configMutations.removeValue(forKey: configID)
                    continue
                }
                if let index = reconciled.firstIndex(where: { $0.id == configID }) {
                    reconciled[index] = config
                } else {
                    reconciled.insert(config, at: 0)
                }
            case .remove:
                reconciled.removeAll { $0.id == configID }
                if versionedMutation.revision <= requestStartRevision,
                   !loadedConfigIDs.contains(configID) {
                    configMutations.removeValue(forKey: configID)
                }
            }
        }
        return reconciled
    }
}

private struct ActiveConfigLoad {
    let id: UUID
    let task: Task<Void, Never>
}
