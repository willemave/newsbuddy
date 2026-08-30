//
//  ProcessingStatsView.swift
//  newsly
//
//  Created by Assistant on 1/16/26.
//

import SwiftUI

struct ProcessingStatsView: View {
    @Environment(BadgeStatsStore.self) private var badgeStatsStore
    @State private var sourcesViewModel = RootDependencyFactory.makeScraperSettingsViewModel(
        filterTypes: ["substack", "atom", "youtube", "podcast_rss"]
    )

    var body: some View {
        List {
            Section {
                statRow(
                    title: "Processing",
                    subtitle: "Pending or running",
                    count: badgeStatsStore.longFormProcessingCount,
                    icon: "clock.arrow.circlepath",
                    color: .brandPrimary
                )
                statRow(
                    title: "Unread",
                    subtitle: "Ready to read",
                    count: badgeStatsStore.longFormCount,
                    icon: "tray",
                    color: .onSurfaceSecondary
                )
            } header: {
                Text("Long-form")
            } footer: {
                Text("Counts include articles and podcasts.")
            }

            if let error = sourcesViewModel.errorMessage {
                Section {
                    HStack(spacing: 12) {
                        Image(systemName: "exclamationmark.circle")
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .accessibilityHidden(true)
                        Text(error)
                            .font(.appCaption)
                            .foregroundStyle(Color.onSurfaceSecondary)
                        Spacer(minLength: 8)
                        Button("Try Again") {
                            Task { await sourcesViewModel.loadConfigsWithDeferredStats() }
                        }
                        .buttonStyle(.bordered)
                    }
                    .accessibilityIdentifier("processing_stats.sources_error")
                }
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
            async let sourcesRefresh: Void = sourcesViewModel.loadConfigs()
            async let badgeStatsRefresh: Void = badgeStatsStore.refreshStats()
            _ = await (sourcesRefresh, badgeStatsRefresh)
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
                .font(.appSymbol(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 28, height: 28)
                .background(color.gradient)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(subtitle)
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            Spacer()
            Text("\(count)")
                .font(.appCallout)
                .fontWeight(.semibold)
                .foregroundStyle(Color.onSurface)
                .monospacedDigit()
                .contentTransition(.numericText())
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(subtitle)")
        .accessibilityValue("\(count)")
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
                .font(.appSymbol(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 28, height: 28)
                .background(Color.brandPrimary.gradient)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(summary)
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityValue(summary)
    }

    private func sourceStatsRow(_ config: ScraperConfig) -> some View {
        let title = config.displayName ?? config.feedURL ?? "Source"
        let meta = sourceMetaLine(config.stats)
        let unreadCount = config.stats?.unreadCount ?? 0
        let unreadSummary: String? = unreadCount > 0 ? "\(unreadCount) unread" : nil

        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 12) {
                SourceTypeIcon(type: config.scraperType)
                    .accessibilityHidden(true)
                Text(title)
                    .font(.appCallout)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)
                Spacer()
                if let unreadSummary {
                    Text(unreadSummary)
                        .font(.appCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            if let meta {
                Text(meta)
                    .font(.appCaption)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .padding(.leading, 40)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(sourceStatsAccessibilityLabel(title: title, unreadSummary: unreadSummary, meta: meta))
    }

    private func sourceStatsAccessibilityLabel(title: String, unreadSummary: String?, meta: String?) -> String {
        [title, unreadSummary, meta]
            .compactMap { $0 }
            .joined(separator: ", ")
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
        formatter.unitsStyle = .full
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
    .environment(BadgeStatsStore())
}
