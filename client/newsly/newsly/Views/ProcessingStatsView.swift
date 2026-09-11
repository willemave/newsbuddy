import SwiftUI

struct ProcessingStatsView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var sourcesViewModel: ScraperSettingsViewModel

    init(sourcesViewModel: ScraperSettingsViewModel) {
        self._sourcesViewModel = State(initialValue: sourcesViewModel)
    }

    var body: some View {
        List {
            if let error = sourcesViewModel.errorMessage {
                Section {
                    Text(error)
                    Button("Try Again") { Task { await sourcesViewModel.loadConfigs() } }
                }
            }
            if sourcesViewModel.configs.isEmpty {
                if sourcesViewModel.isLoading {
                    ProgressView("Loading feeds…")
                } else if sourcesViewModel.errorMessage == nil {
                    ContentUnavailableView("No feeds yet", systemImage: "dot.radiowaves.left.and.right",
                                           description: Text("Add an article or podcast feed in Sources."))
                }
            }
            feedSection("Article Feeds", types: ["substack", "atom", "youtube"])
            feedSection("Podcasts", types: ["podcast_rss"])
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Color.surfacePrimary)
        .navigationTitle("Feed Status")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("feed_status.list")
        .refreshable { await sourcesViewModel.loadConfigs(showLoading: false) }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await sourcesViewModel.loadConfigs()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                await sourcesViewModel.loadConfigs(showLoading: false)
            }
        }
    }

    @ViewBuilder
    private func feedSection(_ title: String, types: [String]) -> some View {
        let feeds = sourcesViewModel.configs.filter { types.contains($0.scraperType) }
            .sorted { ($0.displayName ?? "").localizedStandardCompare($1.displayName ?? "") == .orderedAscending }
        if !feeds.isEmpty {
            Section {
                ForEach(feeds) { config in
                    NavigationLink {
                        FeedHistoryView(config: config, sourcesViewModel: sourcesViewModel)
                    } label: {
                        FeedStatusRow(config: config)
                    }
                    .listRowBackground(Color.surfaceSecondary)
                    .listRowSeparatorTint(Color.outlineVariant)
                    .accessibilityIdentifier("feed_status.source.\(config.id)")
                }
            } header: {
                HStack(alignment: .firstTextBaseline) {
                    Text(title).font(.appHeadline).foregroundStyle(Color.onSurface)
                    Spacer()
                    Text("\(feeds.count) \(feeds.count == 1 ? "feed" : "feeds")")
                        .font(.appCaption).foregroundStyle(Color.onSurfaceSecondary)
                }
                .textCase(nil)
                .padding(.bottom, 6)
            }
        }
    }
}

private struct FeedStatusRow: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    let config: ScraperConfig

    private var hasWork: Bool {
        (config.stats?.processingCount ?? 0) > 0
    }

    private var symbol: String {
        switch config.scraperType {
        case "podcast_rss": "waveform"
        case "youtube": "play.rectangle"
        default: "newspaper"
        }
    }

    private var activity: String {
        var parts: [String] = []
        if !config.isActive { parts.append("Paused") }
        if let stats = config.stats {
            if stats.runningCount > 0 { parts.append("\(stats.runningCount) running") }
            if stats.queuedCount > 0 { parts.append("\(stats.queuedCount) queued") }
            if parts.isEmpty { parts.append("No active work") }
        } else if parts.isEmpty {
            parts.append("Stats unavailable")
        }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: symbol)
                    .font(.appSymbol(size: 15, weight: .medium))
                    .foregroundStyle(Color.brandPrimary)
                    .frame(width: 30, height: 30)
                    .background(Color.surfaceTertiary, in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(config.displayName ?? "Feed")
                        .font(.appHeadline)
                        .foregroundStyle(Color.onSurface)
                    Text(activity)
                        .font(.appCaption.weight(hasWork ? .semibold : .regular))
                        .foregroundStyle(hasWork ? Color.brandPrimary : Color.onSurfaceSecondary)
                }
                .fixedSize(horizontal: false, vertical: true)
            }

            if let stats = config.stats {
                let layout = dynamicTypeSize.isAccessibilitySize
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 10))
                    : AnyLayout(HStackLayout(alignment: .top, spacing: 24))
                layout {
                    metric("Last processed", value: stats.latestProcessedAt.map {
                        $0.formatted(.relative(presentation: .numeric, unitsStyle: .abbreviated))
                    } ?? "Not yet")
                    metric("Total processed", value: stats.completedCount.formatted())
                }
                if let issue = stats.issueSummary {
                    Label(issue, systemImage: "exclamationmark.triangle.fill")
                        .font(.appCaption.weight(.semibold))
                        .foregroundStyle(Color.statusDestructive)
                }
                if stats.ingestionError != nil {
                    Label("Could not refresh feed", systemImage: "exclamationmark.triangle.fill")
                        .font(.appCaption.weight(.semibold))
                        .foregroundStyle(Color.statusDestructive)
                }
            }
        }
        .padding(.vertical, 4)
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .combine)
    }

    private func metric(_ title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.appCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
            Text(value)
                .font(.appSans(size: 16, relativeTo: .callout, weight: .medium))
                .foregroundStyle(Color.onSurface)
                .contentTransition(.numericText())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
    }
}

struct FeedStatusSummary: View {
    let stats: ScraperConfigStats?

    var body: some View {
        if let stats {
            VStack(alignment: .leading, spacing: 4) {
                if let date = stats.latestProcessedAt {
                    Text("Last processed \(date, style: .relative) ago")
                } else {
                    Text("Nothing processed yet")
                }
                Text("\(stats.completedCount) processed · \(stats.runningCount) running · \(stats.queuedCount) queued")
                if let issue = stats.issueSummary {
                    Label(issue, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(Color.statusDestructive)
                }
                if stats.ingestionError != nil {
                    Label("Could not refresh feed", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(Color.statusDestructive)
                }
            }
            .font(.appCaption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        } else {
            Text("Stats unavailable").font(.appCaption).foregroundStyle(.secondary)
        }
    }
}
