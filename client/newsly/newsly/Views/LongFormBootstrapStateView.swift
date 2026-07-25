//
//  LongFormBootstrapStateView.swift
//  newsly
//

import SwiftUI

struct LongFormBootstrapStateView: View {
    let sources: [ScraperConfig]
    let isLoading: Bool
    let onRefresh: () async -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                EditorialMastheadHeader(title: "Long Read")

                VStack(alignment: .leading, spacing: 24) {
                    headlineBlock
                    sourcesBlock
                    loadingRow
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }
            .padding(.bottom, 32)
        }
        .refreshable {
            await onRefresh()
        }
    }

    private var headlineBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: Self.totalSourceItemsProcessing(in: sources) > 0 ? "clock.arrow.circlepath" : "dot.radiowaves.left.and.right")
                    .font(.appSymbol(size: 16, weight: .semibold))
                    .foregroundStyle(Color.brandPrimary)

                Text(Self.bootstrapHeadline(sources: sources))
                    .font(.appTitle3.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
            }

            Text(Self.bootstrapSubtitle(sources: sources))
                .font(.listSubtitle)
                .foregroundStyle(Color.onSurfaceSecondary)

            Text(Self.bootstrapCheckBackSummary(sources: sources))
                .font(.listSubtitle.weight(.medium))
                .foregroundStyle(Color.brandPrimary)
        }
    }

    private var sourcesBlock: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Selected Sources")
                .font(.appHeadline)
                .foregroundStyle(Color.onSurface)
                .padding(.bottom, 12)

            ForEach(sources) { config in
                sourceProgressRow(config)
                if config.id != sources.last?.id {
                    Divider()
                        .padding(.leading, 40)
                }
            }
        }
    }

    @ViewBuilder
    private var loadingRow: some View {
        if isLoading && sources.isEmpty {
            HStack(spacing: 10) {
                ProgressView()
                Text("Loading your sources")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(.top, 4)
        }
    }

    private func sourceProgressRow(_ config: ScraperConfig) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 12) {
                SourceTypeIcon(type: config.scraperType)

                VStack(alignment: .leading, spacing: 2) {
                    Text(config.displayName ?? config.feedURL ?? "Source")
                        .font(.listTitle)
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(1)

                    Text(Self.sourceProgressSummary(for: config))
                        .font(.appCaption)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                }

                Spacer(minLength: 8)

                if let stats = config.stats, stats.processingCount > 0 {
                    ProgressView()
                        .scaleEffect(0.85)
                }
            }
            .padding(.vertical, Spacing.rowVertical)
        }
    }

    static func totalProcessedSourceItems(in sources: [ScraperConfig]) -> Int {
        sources.reduce(0) { partial, config in
            partial + (config.stats?.completedCount ?? 0)
        }
    }

    static func totalSourceItemsProcessing(in sources: [ScraperConfig]) -> Int {
        sources.reduce(0) { partial, config in
            partial + (config.stats?.processingCount ?? 0)
        }
    }

    static func compareSources(_ lhs: ScraperConfig, _ rhs: ScraperConfig) -> Bool {
        let leftProcessing = lhs.stats?.processingCount ?? 0
        let rightProcessing = rhs.stats?.processingCount ?? 0
        if leftProcessing != rightProcessing {
            return leftProcessing > rightProcessing
        }

        let leftCompleted = lhs.stats?.completedCount ?? 0
        let rightCompleted = rhs.stats?.completedCount ?? 0
        if leftCompleted != rightCompleted {
            return leftCompleted > rightCompleted
        }

        let leftName = lhs.displayName ?? lhs.feedURL ?? ""
        let rightName = rhs.displayName ?? rhs.feedURL ?? ""
        return leftName.localizedCaseInsensitiveCompare(rightName) == .orderedAscending
    }

    private static func sourcesReadyCount(in sources: [ScraperConfig]) -> Int {
        sources.filter { ($0.stats?.completedCount ?? 0) > 0 }.count
    }

    private static func bootstrapHeadline(sources: [ScraperConfig]) -> String {
        if totalSourceItemsProcessing(in: sources) > 0 {
            return "Your long-form feed is being assembled"
        }
        if sourcesReadyCount(in: sources) > 0 {
            return "Your sources are connected"
        }
        return "Waiting for the first long-form items"
    }

    private static func bootstrapSubtitle(sources: [ScraperConfig]) -> String {
        let processing = totalSourceItemsProcessing(in: sources)
        if processing > 0 {
            return "\(processing) items are still processing across \(sources.count) sources."
        }
        let ready = sourcesReadyCount(in: sources)
        if ready > 0 {
            return "\(ready) of \(sources.count) sources have published something, but nothing is ready in this tab yet."
        }
        return "We already know the feeds and podcasts you picked. This tab will fill in as their first items are fetched and processed."
    }

    private static let bootstrapRelativeFormatter: RelativeDateTimeFormatter = {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter
    }()

    private static func bootstrapCheckBackSummary(sources: [ScraperConfig]) -> String {
        if totalSourceItemsProcessing(in: sources) > 0 {
            return "Check back in a minute."
        }

        let predictions = sources.compactMap(\.stats)
        if let earliest = predictions.compactMap(\.nextExpectedDate).min() {
            let relative = bootstrapRelativeFormatter.localizedString(for: earliest, relativeTo: Date())
            return earliest > Date() ? "Check back \(relative)." : "Check back later today."
        }

        if totalProcessedSourceItems(in: sources) == 0 {
            return "Check back after the first source finishes processing."
        }

        return "Check back later today."
    }

    private static func sourceProgressSummary(for config: ScraperConfig) -> String {
        guard let stats = config.stats else {
            return "Waiting for the first fetch"
        }

        var parts: [String] = []
        if stats.completedCount > 0 {
            let suffix = stats.completedCount == 1 ? "item" : "items"
            parts.append("\(stats.completedCount) processed \(suffix)")
        }
        if stats.processingCount > 0 {
            let suffix = stats.processingCount == 1 ? "item" : "items"
            parts.append("\(stats.processingCount) processing \(suffix)")
        }
        if let nextExpected = stats.nextExpectedSummary {
            parts.append(nextExpected)
        } else if let processed = stats.relativeProcessedSummary {
            parts.append(processed)
        }

        return parts.isEmpty ? "Waiting for the first fetch" : parts.joined(separator: " • ")
    }
}
