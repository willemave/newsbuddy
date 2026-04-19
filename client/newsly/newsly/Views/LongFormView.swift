//
//  LongFormView.swift
//  newsly
//
//  Created by Assistant on 11/4/25.
//

import SwiftUI

struct LongFormView: View {
    @ObservedObject var viewModel: LongContentListViewModel
    let isActive: Bool
    let onSelect: (ContentDetailRoute) -> Void

    @StateObject private var processingCountService = ProcessingCountService.shared
    @StateObject private var unreadCountService = UnreadCountService.shared
    @StateObject private var sourcesViewModel = ScraperSettingsViewModel(
        filterTypes: ["substack", "atom", "youtube", "podcast_rss"]
    )
    @State private var showMarkAllConfirmation = false
    @State private var isProcessingBulk = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                if viewModel.state == .initialLoading && viewModel.currentItems().isEmpty {
                    LoadingView()
                } else if case .error(let error) = viewModel.state, viewModel.currentItems().isEmpty {
                    ErrorView(message: error.localizedDescription) {
                        viewModel.refreshTrigger.send(())
                    }
                } else {
                    if viewModel.currentItems().isEmpty {
                        longFormEmptyState
                    } else {
                        ScrollView {
                            LazyVStack(spacing: 0) {
                                // Editorial header
                                Text("Longread")
                                    .font(.terracottaDisplayLarge)
                                    .foregroundStyle(Color.onSurface)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.horizontal, Spacing.screenHorizontal)
                                    .padding(.top, 16)
                                    .padding(.bottom, 24)

                                VStack(spacing: CardMetrics.cardSpacing) {
                                    let items = viewModel.currentItems()
                                    let groups = bentoGroups(from: items)
                                    ForEach(groups.indices, id: \.self) { groupIndex in
                                        let group = groups[groupIndex]
                                        bentoGroupView(group: group, allItems: items)
                                    }
                                }
                                .padding(.horizontal, Spacing.screenHorizontal)

                                if viewModel.state == .loadingMore {
                                    HStack {
                                        Spacer()
                                        ProgressView()
                                            .padding()
                                        Spacer()
                                    }
                                }
                            }
                            .padding(.vertical, 12)
                        }
                        .refreshable {
                            await refreshLongFormSurface(forceReload: true)
                        }
                        .simultaneousGesture(
                            LongPressGesture(minimumDuration: 0.8).onEnded { _ in
                                if viewModel.currentItems().contains(where: { !$0.isRead }) {
                                    showMarkAllConfirmation = true
                                }
                            }
                        )
                        .confirmationDialog(
                            "Mark all long-form content as read?",
                            isPresented: $showMarkAllConfirmation
                        ) {
                            Button("Mark All as Read", role: .destructive) {
                                showMarkAllConfirmation = false
                                isProcessingBulk = true
                                Task {
                                    defer { isProcessingBulk = false }
                                    await viewModel.markAllVisibleAsRead()
                                }
                            }
                            Button("Cancel", role: .cancel) {
                                showMarkAllConfirmation = false
                            }
                        } message: {
                            Text("Long press to quickly mark every unread item in the current list as read.")
                        }
                    }
                }
            }
            .task(id: shouldPollLongForm) {
                guard shouldPollLongForm else { return }
                await runLongFormPollingLoop()
            }

            if isProcessingBulk {
                Color.black.opacity(0.15)
                    .ignoresSafeArea()
                ProgressView("Marking content")
                    .padding(16)
                    .background(Color.surfacePrimary)
                    .cornerRadius(12)
            }
        }
        .screenContainer()
        .accessibilityIdentifier("long.screen")
    }

    private var shouldPollLongForm: Bool {
        isActive && scenePhase == .active
    }

    // MARK: - Bento Grid Layout

    private struct BentoGroup {
        enum Layout {
            case hero(ContentSummary)
            case pair(ContentSummary, ContentSummary)
            case single(ContentSummary)
        }
        let layout: Layout
    }

    private func bentoGroups(from items: [ContentSummary]) -> [BentoGroup] {
        var groups: [BentoGroup] = []
        var index = 0
        while index < items.count {
            // Hero card
            groups.append(BentoGroup(layout: .hero(items[index])))
            index += 1
            // Side pair (if two more items available)
            if index + 1 < items.count {
                groups.append(BentoGroup(layout: .pair(items[index], items[index + 1])))
                index += 2
            } else if index < items.count {
                groups.append(BentoGroup(layout: .single(items[index])))
                index += 1
            }
        }
        return groups
    }

    @ViewBuilder
    private func bentoGroupView(group: BentoGroup, allItems: [ContentSummary]) -> some View {
        switch group.layout {
        case .hero(let content):
            cardLink(content: content, variant: .hero, allItems: allItems)

        case .pair(let left, let right):
            HStack(spacing: 12) {
                cardLink(content: left, variant: .compact, allItems: allItems)
                cardLink(content: right, variant: .compact, allItems: allItems)
            }

        case .single(let content):
            cardLink(content: content, variant: .compact, allItems: allItems)
        }
    }

    @ViewBuilder
    private func cardLink(content: ContentSummary, variant: LongFormCard.Variant, allItems: [ContentSummary]) -> some View {
        NavigationLink(
            value: ContentDetailRoute(
                summary: content,
                allContentIds: allItems.map(\.id)
            )
        ) {
            LongFormCard(
                content: content,
                variant: variant,
                onMarkRead: {
                    viewModel.markAsRead(content.id)
                },
                onToggleKnowledgeSave: {
                    Task {
                        await viewModel.toggleKnowledgeSave(content.id)
                    }
                }
            )
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("long.row.\(content.id)")
        .onAppear {
            if content.id == allItems.last?.id {
                viewModel.loadMoreTrigger.send(())
            }
        }
    }

    @ViewBuilder
    private var longFormEmptyState: some View {
        if totalProcessedSourceItems == 0 && !longFormSources.isEmpty {
            longFormBootstrapState
        } else if processingCountService.longFormProcessingCount > 0
            && totalProcessedSourceItems == 0
        {
            VStack(spacing: 16) {
                Spacer()
                ProgressView()
                Text("Preparing \(processingCountService.longFormProcessingCount) long-form items")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if unreadCountService.longFormCount == 0 && totalProcessedSourceItems > 0 {
            EmptyStateView(
                icon: "checkmark.circle",
                title: "You're All Caught Up",
                subtitle: "No unread long-form content right now"
            )
        } else {
            EmptyStateView(
                icon: "doc.richtext",
                title: "No Long-Form Content",
                subtitle: "Articles and podcasts will appear here once processed"
            )
        }
    }

    private var longFormSources: [ScraperConfig] {
        sourcesViewModel.configs
            .filter { $0.isActive }
            .sorted(by: compareSources)
    }

    private var sourcesReadyCount: Int {
        longFormSources.filter { ($0.stats?.completedCount ?? 0) > 0 }.count
    }

    private var totalProcessedSourceItems: Int {
        longFormSources.reduce(0) { partial, config in
            partial + (config.stats?.completedCount ?? 0)
        }
    }

    private var totalSourceItemsProcessing: Int {
        longFormSources.reduce(0) { partial, config in
            partial + (config.stats?.processingCount ?? 0)
        }
    }

    private var bootstrapHeadline: String {
        if totalSourceItemsProcessing > 0 {
            return "Your long-form feed is being assembled"
        }
        if sourcesReadyCount > 0 {
            return "Your sources are connected"
        }
        return "Waiting for the first long-form items"
    }

    private var bootstrapSubtitle: String {
        if totalSourceItemsProcessing > 0 {
            return "\(totalSourceItemsProcessing) items are still processing across \(longFormSources.count) sources."
        }
        if sourcesReadyCount > 0 {
            return "\(sourcesReadyCount) of \(longFormSources.count) sources have published something, but nothing is ready in this tab yet."
        }
        return "We already know the feeds and podcasts you picked. This tab will fill in as their first items are fetched and processed."
    }

    private var bootstrapCheckBackSummary: String {
        if totalSourceItemsProcessing > 0 {
            return "Check back in a minute."
        }

        let predictions = longFormSources.compactMap(\.stats)
        if let earliest = predictions.compactMap(\.nextExpectedDate).min() {
            let formatter = RelativeDateTimeFormatter()
            formatter.unitsStyle = .short
            let relative = formatter.localizedString(for: earliest, relativeTo: Date())
            return earliest > Date() ? "Check back \(relative)." : "Check back later today."
        }

        if totalProcessedSourceItems == 0 {
            return "Check back after the first source finishes processing."
        }

        return "Check back later today."
    }

    private var longFormBootstrapState: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Longread")
                    .font(.terracottaDisplayLarge)
                    .foregroundStyle(Color.onSurface)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 16)

                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 10) {
                        Image(systemName: totalSourceItemsProcessing > 0 ? "clock.arrow.circlepath" : "dot.radiowaves.left.and.right")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(Color.terracottaPrimary)

                        Text(bootstrapHeadline)
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(Color.onSurface)
                    }

                    Text(bootstrapSubtitle)
                        .font(.listSubtitle)
                        .foregroundStyle(Color.onSurfaceSecondary)

                    Text(bootstrapCheckBackSummary)
                        .font(.listSubtitle.weight(.medium))
                        .foregroundStyle(Color.terracottaPrimary)
                }

                VStack(alignment: .leading, spacing: 0) {
                    Text("Selected Sources")
                        .font(.headline)
                        .foregroundStyle(Color.onSurface)
                        .padding(.bottom, 12)

                    ForEach(longFormSources) { config in
                        sourceProgressRow(config)
                        if config.id != longFormSources.last?.id {
                            Divider()
                                .padding(.leading, 40)
                        }
                    }
                }

                if sourcesViewModel.isLoading && longFormSources.isEmpty {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Loading your sources")
                            .font(.listSubtitle)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    .padding(.top, 4)
                }
            }
            .padding(.horizontal, Spacing.screenHorizontal)
            .padding(.bottom, 32)
        }
        .refreshable {
            await refreshLongFormSurface(forceReload: true)
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

                    Text(sourceProgressSummary(for: config))
                        .font(.caption)
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

    private func sourceProgressSummary(for config: ScraperConfig) -> String {
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

    private func compareSources(_ lhs: ScraperConfig, _ rhs: ScraperConfig) -> Bool {
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

    @MainActor
    private func refreshLongFormSurface(forceReload: Bool) async {
        if forceReload {
            if viewModel.currentItems().isEmpty {
                viewModel.refreshUnreadFeed()
            } else {
                viewModel.refreshUnreadFeedInBackground()
            }
        } else {
            viewModel.ensureUnreadFeedLoaded()
        }

        async let unreadRefresh: Void = unreadCountService.refreshCounts()
        async let processingRefresh: Void = refreshProcessingCountIfNeeded()
        async let sourcesRefresh: Void = refreshSourcesIfNeeded()
        _ = await (unreadRefresh, processingRefresh, sourcesRefresh)
    }

    @MainActor
    private func runLongFormPollingLoop() async {
        await refreshLongFormSurface(forceReload: true)

        while !Task.isCancelled {
            do {
                try await Task.sleep(for: .seconds(5))
            } catch {
                break
            }

            guard shouldPollLongForm else { break }
            await refreshLongFormSurface(forceReload: true)
        }
    }

    @MainActor
    private func refreshProcessingCountIfNeeded() async {
        guard viewModel.currentItems().isEmpty || processingCountService.longFormProcessingCount > 0 else {
            return
        }
        await processingCountService.refreshCount()
    }

    @MainActor
    private func refreshSourcesIfNeeded() async {
        guard sourcesViewModel.configs.isEmpty else { return }
        await sourcesViewModel.loadConfigs()
    }
}
