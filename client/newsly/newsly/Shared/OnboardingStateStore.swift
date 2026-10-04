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
    /// Selected topic values keyed by aggregator key.
    var selectedAggregatorTopics: [String: [String]] = [:]
    var discoveryRunId: Int?
    var discoveryRunStatus: String?
    var discoveryErrorMessage: String?
    var hasReachedPollingLimit: Bool
    var topicSummary: String?
    var inferredTopics: [String]
}

// Backwards-compatible decoder: snapshots written before fast-news landed have
// no `selectedAggregators` key. Defining the decoder
// in an extension preserves Swift's synthesized memberwise init.
extension OnboardingProgressSnapshot {
    // Snapshots saved before per-aggregator topics stored Brutalist topics under
    // `selectedBrutalistTopics`. Remove this legacy key once app builds that wrote
    // it (pre-arXiv onboarding, October 2026) can no longer have in-progress
    // onboarding saved on device.
    private enum LegacyCodingKeys: String, CodingKey {
        case selectedBrutalistTopics
    }

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
        if let topics = try? container.decode(
            [String: [String]].self, forKey: .selectedAggregatorTopics)
        {
            selectedAggregatorTopics = topics
        } else {
            let legacy = try decoder.container(keyedBy: LegacyCodingKeys.self)
            let brutalistTopics =
                (try? legacy.decode([String].self, forKey: .selectedBrutalistTopics)) ?? []
            selectedAggregatorTopics = brutalistTopics.isEmpty ? [:] : ["brutalist": brutalistTopics]
        }
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
    private let progressKey = "onboarding_progress"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = SharedContainer.userDefaults) {
        self.defaults = defaults
    }

    func saveProgress(userId: Int, snapshot: OnboardingProgressSnapshot) {
        guard userId > 0 else { return }
        var progress = loadProgressMap()
        progress[String(userId)] = snapshot
        saveProgressMap(progress)
    }

    func progress(userId: Int) -> OnboardingProgressSnapshot? {
        guard userId > 0 else { return nil }
        let key = String(userId)
        return loadProgressMap()[key]
    }

    func discoveryRunId(userId: Int) -> Int? {
        progress(userId: userId)?.discoveryRunId
    }

    func clearProgress(userId: Int) {
        guard userId > 0 else { return }
        let key = String(userId)
        var progress = loadProgressMap()
        progress.removeValue(forKey: key)
        saveProgressMap(progress)
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
}
