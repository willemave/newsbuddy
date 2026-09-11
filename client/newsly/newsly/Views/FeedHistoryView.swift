import SwiftUI

struct FeedHistoryView: View {
    @Environment(ReadStateCache.self) private var readStateCache
    @Environment(\.scenePhase) private var scenePhase
    let config: ScraperConfig
    let sourcesViewModel: ScraperSettingsViewModel
    @State private var viewModel: FeedHistoryViewModel
    @State private var status: String

    init(config: ScraperConfig, sourcesViewModel: ScraperSettingsViewModel) {
        self.config = config
        self.sourcesViewModel = sourcesViewModel
        _viewModel = State(initialValue: FeedHistoryViewModel(configId: config.id))
        _status = State(initialValue: (config.stats?.failedCount ?? 0) > 0 ? "failed" : "completed")
    }

    private var currentConfig: ScraperConfig {
        sourcesViewModel.configs.first { $0.id == config.id } ?? config
    }

    var body: some View {
        List {
            Section {
                if !currentConfig.isActive { Text("Feed paused") }
                FeedStatusSummary(stats: currentConfig.stats)
                if let checked = currentConfig.stats?.lastFetchAt {
                    Text("Last successful check \(checked, style: .relative) ago")
                        .font(.appCaption).foregroundStyle(.secondary)
                }
            }
            if status == "completed", !viewModel.activeItems.isEmpty {
                Section("Current work") {
                    ForEach(Array(viewModel.activeItems.prefix(5)), id: \.id) { item in
                        historyRow(item)
                    }
                    if viewModel.activeItems.count > 5 {
                        Button("View all current work") { status = "active" }
                    }
                }
            }
            Section {
                Picker("Show", selection: $status) {
                    Text("Processed").tag("completed")
                    Text("Current work").tag("active")
                    Text("Issues").tag("failed")
                    Text("All items").tag("all")
                }
                .accessibilityIdentifier("feed_history.filter")
                ForEach(viewModel.items, id: \.id) { item in
                    if item.status == "completed" {
                        NavigationLink {
                            ContentDetailView(contentId: item.id, contentType: APIContentType(rawValue: item.contentType),
                                              readStateCache: readStateCache)
                        } label: { historyRow(item) }
                    } else {
                        historyRow(item)
                    }
                }
                if viewModel.isLoading {
                    ProgressView("Loading history…")
                } else if viewModel.items.isEmpty, viewModel.errorMessage == nil {
                    Text(emptyMessage).foregroundStyle(.secondary)
                }
                if let error = viewModel.errorMessage {
                    Text(error).foregroundStyle(.secondary)
                    Button("Try Again") { Task { await refresh() } }
                }
                if viewModel.nextOffset != nil {
                    Button("Load more") { Task { await viewModel.loadMore() } }
                        .disabled(viewModel.isLoading)
                }
            } header: {
                Text("History")
            } footer: {
                Text(status == "completed"
                     ? "Includes read and archived items. Most recently processed first."
                     : "Includes read and archived items.")
            }
        }
        .navigationTitle(currentConfig.displayName ?? "Feed History")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("feed_history.list")
        .refreshable { await refresh() }
        .task(id: status) { await refresh() }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                await viewModel.refreshActivity()
                await sourcesViewModel.loadConfigs(showLoading: false)
            }
        }
    }

    private var emptyMessage: String {
        switch status {
        case "active": "No current work."
        case "failed": "No failed or cancelled items."
        case "all": "No items from this feed yet."
        default: "Nothing processed yet."
        }
    }

    private func refresh() async {
        async let stats: Void = sourcesViewModel.loadConfigs(showLoading: false)
        await viewModel.refresh(status: status)
        await stats
    }

    private func historyRow(_ item: APIFeedHistoryItem) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(item.title).font(.appCallout).foregroundStyle(Color.onSurface)
            if let processed = item.processedAt {
                Text("Processed \(processed, style: .relative) ago")
            } else {
                Text(statusLabel(item))
            }
            if let published = item.publicationAt {
                Text("Published \(published.formatted(date: .abbreviated, time: .omitted))")
            }
            if let seconds = item.durationSeconds, seconds > 0 {
                Text("\(max(1, seconds / 60)) min episode")
            } else if item.contentType == "article", let minutes = item.readingMinutes {
                Text("About \(minutes) min read")
            }
        }
        .font(.appCaption)
        .foregroundStyle(.secondary)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.vertical, 3)
        .accessibilityIdentifier("feed_history.item.\(item.id)")
    }

    private func statusLabel(_ item: APIFeedHistoryItem) -> String {
        let state: String = switch item.status {
        case "running": "Running"
        case "queued": "Queued"
        case "failed": "Failed"
        case "cancelled": "Cancelled"
        case "skipped": "Skipped"
        default: "Waiting for processing"
        }
        let stage: String? = switch item.stage {
        case "process_content": "Preparing article"
        case "process_podcast_media": "Preparing podcast"
        case "summarize": "Summarizing"
        case "generate_image": "Creating artwork"
        default: nil
        }
        return [state, stage].compactMap { $0 }.joined(separator: " · ")
    }
}
