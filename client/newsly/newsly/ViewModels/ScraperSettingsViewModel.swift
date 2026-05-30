//
//  ScraperSettingsViewModel.swift
//  newsly
//

import Foundation

@MainActor
class ScraperSettingsViewModel: ObservableObject {
    @Published var configs: [ScraperConfig] = []
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    private let filterTypes: [String]?
    private let service = ScraperConfigService.shared

    init(filterTypes: [String]? = nil) {
        self.filterTypes = filterTypes
    }

    func loadConfigs(includeStats: Bool = true, showLoading: Bool = true) async {
        print("DEBUG: ScraperSettingsViewModel.loadConfigs() called")
        if showLoading {
            isLoading = true
        }
        errorMessage = nil
        do {
            configs = try await service.listConfigs(
                types: filterTypes,
                includeStats: includeStats
            )
            print("DEBUG: Successfully loaded \(configs.count) scraper configs")
            for config in configs {
                print("DEBUG: Config: \(config.displayName ?? "N/A") (\(config.scraperType))")
            }
        } catch {
            print("DEBUG: Error loading scraper configs: \(error)")
            errorMessage = error.localizedDescription
        }
        if showLoading {
            isLoading = false
        }
    }

    func loadConfigsWithDeferredStats() async {
        await loadConfigs(includeStats: false)
        await loadConfigs(includeStats: true, showLoading: false)
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
