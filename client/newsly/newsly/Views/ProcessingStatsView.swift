//
//  ProcessingStatsView.swift
//  newsly
//
//  Created by Assistant on 1/16/26.
//

import SwiftUI

struct ProcessingStatsView: View {
    @StateObject private var processingCountService = ProcessingCountService.shared
    @StateObject private var unreadCountService = UnreadCountService.shared
    @StateObject private var sourcesViewModel = ScraperSettingsViewModel(
        filterTypes: ["substack", "atom", "youtube", "podcast_rss"]
    )

    var body: some View {
        List {
            Section {
                statRow(
                    title: "Processing",
                    subtitle: "Pending or running",
                    count: processingCountService.longFormProcessingCount,
                    icon: "clock.arrow.circlepath",
                    color: .teal
                )
                statRow(
                    title: "Unread",
                    subtitle: "Ready to read",
                    count: unreadCountService.longFormCount,
                    icon: "tray",
                    color: .blue
                )
            } header: {
                Text("Long-form")
            } footer: {
                Text("Counts include articles and podcasts.")
            }

            if !articleSources.isEmpty || !podcastSources.isEmpty {
                Section {
                    if let articlePrediction = nextExpectedSummary(for: articleSources, title: "Articles") {
                        predictionRow(title: "Articles", summary: articlePrediction)
                    }
                    if let podcastPrediction = nextExpectedSummary(for: podcastSources, title: "Podcasts") {
                        predictionRow(title: "Podcasts", summary: podcastPrediction)
                    }
                } header: {
                    Text("Expected")
                }
            }

            if !articleSources.isEmpty {
                Section {
                    ForEach(articleSources) { config in
                        sourceStatsRow(config)
                    }
                } header: {
                    Text("Article Feeds")
                }
            }

            if !podcastSources.isEmpty {
                Section {
                    ForEach(podcastSources) { config in
                        sourceStatsRow(config)
                    }
                } header: {
                    Text("Podcasts")
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Processing Stats")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            async let unreadRefresh: Void = unreadCountService.refreshCounts()
            async let processingRefresh: Void = processingCountService.refreshCount()
            async let sourcesRefresh: Void = sourcesViewModel.loadConfigs()
            _ = await (unreadRefresh, processingRefresh, sourcesRefresh)
        }
    }

    private func statRow(
        title: String,
        subtitle: String,
        count: Int,
        icon: String,
        color: Color
    ) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 28, height: 28)
                .background(color.gradient)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text("\(count)")
                .font(.callout)
                .fontWeight(.semibold)
                .foregroundStyle(.primary)
        }
        .padding(.vertical, 2)
    }

    private var articleSources: [ScraperConfig] {
        sourcesViewModel.configs
            .filter { ["substack", "atom", "youtube"].contains($0.scraperType) }
            .sorted(by: compareSources)
    }

    private var podcastSources: [ScraperConfig] {
        sourcesViewModel.configs
            .filter { $0.scraperType == "podcast_rss" }
            .sorted(by: compareSources)
    }

    private func compareSources(_ lhs: ScraperConfig, _ rhs: ScraperConfig) -> Bool {
        let leftDate = lhs.stats?.latestProcessedDate ?? .distantPast
        let rightDate = rhs.stats?.latestProcessedDate ?? .distantPast
        if leftDate != rightDate {
            return leftDate > rightDate
        }
        let leftUnread = lhs.stats?.unreadCount ?? 0
        let rightUnread = rhs.stats?.unreadCount ?? 0
        if leftUnread != rightUnread {
            return leftUnread > rightUnread
        }
        return (lhs.displayName ?? lhs.feedURL ?? "") < (rhs.displayName ?? rhs.feedURL ?? "")
    }

    private func predictionRow(title: String, summary: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "sparkles.rectangle.stack")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 28, height: 28)
                .background(Color.orange.gradient)
                .clipShape(RoundedRectangle(cornerRadius: 6))
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(summary)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func sourceStatsRow(_ config: ScraperConfig) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 12) {
                SourceTypeIcon(type: config.scraperType)
                Text(config.displayName ?? config.feedURL ?? "Source")
                    .font(.callout)
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                Spacer()
                if let unreadCount = config.stats?.unreadCount, unreadCount > 0 {
                    Text("\(unreadCount) unread")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if let meta = sourceMetaLine(config.stats) {
                Text(meta)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 40)
            }
        }
        .padding(.vertical, 2)
    }

    private func sourceMetaLine(_ stats: ScraperConfigStats?) -> String? {
        guard let stats, stats.hasVisibleStats else { return nil }

        var parts: [String] = []
        if let countSummary = stats.compactCountSummary {
            parts.append(countSummary)
        }
        if let processed = stats.relativeProcessedSummary {
            parts.append(processed)
        }
        if let nextExpected = stats.nextExpectedSummary {
            parts.append(nextExpected)
        }
        if let cadence = stats.cadenceSummary {
            parts.append(cadence)
        }
        return parts.isEmpty ? nil : parts.joined(separator: " • ")
    }

    private func nextExpectedSummary(for configs: [ScraperConfig], title: String) -> String? {
        let predictions = configs.compactMap(\.stats)
        guard let earliest = predictions.compactMap(\.nextExpectedDate).min() else {
            return nil
        }

        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        let relative = formatter.localizedString(for: earliest, relativeTo: Date())
        let dueSources = predictions.filter { $0.nextExpectedDate == earliest }.count
        let sourceCount = max(dueSources, 1)
        let suffix = sourceCount == 1 ? "source" : "sources"
        return "\(title) likely \(relative) from \(sourceCount) \(suffix)"
    }
}

#Preview {
    NavigationStack {
        ProcessingStatsView()
    }
}
