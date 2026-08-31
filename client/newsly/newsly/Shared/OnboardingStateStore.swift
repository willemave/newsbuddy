//
//  OnboardingStateStore.swift
//  newsly
//
//  Created by Assistant on 1/17/26.
//

import Foundation

struct OnboardingProgressSnapshot: Codable {
    var step: OnboardingStep
    var isPersonalized: Bool
    var suggestions: OnboardingFastDiscoverResponse?
    var selectedSuggestionIds: [Int]
    var selectedAggregators: [String] = []
    var selectedBrutalistTopics: [String] = []
    var discoveryRunId: Int?
    var discoveryRunStatus: String?
    var discoveryErrorMessage: String?
    var hasReachedPollingLimit: Bool
    var topicSummary: String?
    var inferredTopics: [String]
}

// Backwards-compatible decoder: snapshots written before fast-news landed have
// no `selectedAggregators`/`selectedBrutalistTopics` keys. Defining the decoder
// in an extension preserves Swift's synthesized memberwise init.
extension OnboardingProgressSnapshot {
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        step = try container.decode(OnboardingStep.self, forKey: .step)
        isPersonalized = try container.decode(Bool.self, forKey: .isPersonalized)
        suggestions = try container.decodeIfPresent(
            OnboardingFastDiscoverResponse.self, forKey: .suggestions)
        selectedSuggestionIds =
            (try? container.decode([Int].self, forKey: .selectedSuggestionIds)) ?? []
        selectedAggregators =
            (try? container.decode([String].self, forKey: .selectedAggregators)) ?? []
        selectedBrutalistTopics =
            (try? container.decode([String].self, forKey: .selectedBrutalistTopics)) ?? []
        discoveryRunId = try container.decodeIfPresent(Int.self, forKey: .discoveryRunId)
        discoveryRunStatus = try container.decodeIfPresent(String.self, forKey: .discoveryRunStatus)
        discoveryErrorMessage = try container.decodeIfPresent(
            String.self, forKey: .discoveryErrorMessage)
        hasReachedPollingLimit = try container.decode(Bool.self, forKey: .hasReachedPollingLimit)
        topicSummary = try container.decodeIfPresent(String.self, forKey: .topicSummary)
        inferredTopics = try container.decode([String].self, forKey: .inferredTopics)
    }
}

final class OnboardingStateStore {
    static let shared = OnboardingStateStore()

    private let progressKey = "onboarding_progress"
    private let legacyDiscoveryRunKey = "onboarding_discovery_runs"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = SharedContainer.userDefaults) {
        self.defaults = defaults
    }

    func saveProgress(userId: Int, snapshot: OnboardingProgressSnapshot) {
        guard userId > 0 else { return }
        var progress = loadProgressMap()
        progress[String(userId)] = snapshot
        saveProgressMap(progress)
        clearLegacyDiscoveryRun(userId: userId)
    }

    func progress(userId: Int) -> OnboardingProgressSnapshot? {
        guard userId > 0 else { return nil }
        let key = String(userId)
        if let snapshot = loadProgressMap()[key] {
            return snapshot
        }

        guard let legacyRunId = loadLegacyRunMap()[key] else { return nil }
        return OnboardingProgressSnapshot(
            step: .loading,
            isPersonalized: true,
            suggestions: nil,
            selectedSuggestionIds: [],
            discoveryRunId: legacyRunId,
            discoveryRunStatus: nil,
            discoveryErrorMessage: nil,
            hasReachedPollingLimit: false,
            topicSummary: nil,
            inferredTopics: []
        )
    }

    func setDiscoveryRun(userId: Int, runId: Int) {
        guard userId > 0, runId > 0 else { return }
        saveProgress(
            userId: userId,
            snapshot: OnboardingProgressSnapshot(
                step: .loading,
                isPersonalized: true,
                suggestions: nil,
                selectedSuggestionIds: [],
                discoveryRunId: runId,
                discoveryRunStatus: nil,
                discoveryErrorMessage: nil,
                hasReachedPollingLimit: false,
                topicSummary: nil,
                inferredTopics: []
            )
        )
    }

    func discoveryRunId(userId: Int) -> Int? {
        progress(userId: userId)?.discoveryRunId
    }

    func clearDiscoveryRun(userId: Int) {
        clearProgress(userId: userId)
    }

    func clearProgress(userId: Int) {
        guard userId > 0 else { return }
        let key = String(userId)
        var progress = loadProgressMap()
        progress.removeValue(forKey: key)
        saveProgressMap(progress)
        clearLegacyDiscoveryRun(userId: userId)
    }

    private func loadProgressMap() -> [String: OnboardingProgressSnapshot] {
        guard let data = defaults.data(forKey: progressKey) else { return [:] }
        return
            (try? JSONDecoder().decode([String: OnboardingProgressSnapshot].self, from: data)) ?? [:]
    }

    private func saveProgressMap(_ progress: [String: OnboardingProgressSnapshot]) {
        if let data = try? JSONEncoder().encode(progress) {
            defaults.set(data, forKey: progressKey)
        }
    }

    private func loadLegacyRunMap() -> [String: Int] {
        guard let data = defaults.data(forKey: legacyDiscoveryRunKey) else { return [:] }
        return (try? JSONDecoder().decode([String: Int].self, from: data)) ?? [:]
    }

    private func clearLegacyDiscoveryRun(userId: Int) {
        var runs = loadLegacyRunMap()
        runs.removeValue(forKey: String(userId))
        if let data = try? JSONEncoder().encode(runs) {
            defaults.set(data, forKey: legacyDiscoveryRunKey)
        }
    }
}
