//
//  PodcastSourcesView.swift
//  newsly
//

import SwiftUI

private enum PodcastSourcesSheetDestination: Identifiable {
    case addSource
    case sourceDetail(ScraperConfig)

    var id: String {
        switch self {
        case .addSource:
            "addSource"
        case .sourceDetail(let config):
            "sourceDetail.\(config.id)"
        }
    }
}

struct PodcastSourcesView: View {
    @State private var viewModel = RootDependencyFactory.makeScraperSettingsViewModel(
        filterTypes: ["podcast_rss"]
    )
    @State private var activeSheet: PodcastSourcesSheetDestination?
    @State private var newFeedURL: String = ""
    @State private var newFeedName: String = ""
    @State private var newLimit: String = ""
    @State private var addSourceError: String?
    @State private var isAddingSource = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            ScrollView {
                LazyVStack(spacing: 0) {
                    if viewModel.isLoading && viewModel.configs.isEmpty {
                        loadingView
                    } else if viewModel.configs.isEmpty {
                        SettingsEmptyStateView(
                            icon: "waveform",
                            title: "No Podcast Sources",
                            subtitle: "Add podcast RSS feeds to start receiving episodes",
                            actionTitle: "Add Source",
                            action: { activeSheet = .addSource }
                        )
                        .frame(minHeight: 400)
                    } else {
                        sourcesList
                    }

                    if let error = viewModel.errorMessage {
                        errorBanner(error)
                    }
                }
            }
            .refreshable { await viewModel.loadConfigsWithDeferredStats() }

            // Floating add button
            if !viewModel.configs.isEmpty {
                AddButton { activeSheet = .addSource }
                    .padding(Spacing.rowHorizontal)
            }
        }
        .background(Color.surfacePrimary)
        .navigationTitle("Podcast Sources")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.loadConfigsWithDeferredStats() }
        .sheet(item: $activeSheet) { destination in
            switch destination {
            case .addSource:
                addSourceSheet
            case .sourceDetail(let config):
                SourceDetailSheet(viewModel: viewModel, config: config)
            }
        }
    }

    // MARK: - Loading View

    private var loadingView: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("Loading sources...")
                .font(.appSubheadline)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 100)
    }

    // MARK: - Sources List

    private var sourcesList: some View {
        ForEach(viewModel.configs) { config in
            VStack(spacing: 0) {
                SourceRow(
                    name: config.displayName ?? config.feedURL ?? "Podcast",
                    url: config.feedURL,
                    type: config.scraperType,
                    isActive: config.isActive,
                    stats: config.stats
                )
                .onTapGesture { activeSheet = .sourceDetail(config) }

                if config.id != viewModel.configs.last?.id {
                    RowDivider()
                }
            }
            .swipeActions {
                Button(role: .destructive) {
                    Task { await viewModel.deleteConfig(config) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
        }
    }

    // MARK: - Error Banner

    private func errorBanner(_ error: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundStyle(Color.statusDestructive)
            Text(error)
                .font(.appCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
            Spacer(minLength: 8)
            Button("Try Again") {
                Task { await viewModel.loadConfigsWithDeferredStats() }
            }
            .font(.appCaption.weight(.semibold))
            .accessibilityIdentifier("podcast_sources.error.retry")
        }
        .padding()
        .accessibilityIdentifier("podcast_sources.error")
    }

    // MARK: - Add Source Sheet

    private var addSourceSheet: some View {
        NavigationStack {
            VStack(spacing: 24) {
                // URL field
                VStack(alignment: .leading, spacing: 8) {
                    Text("FEED URL")
                        .font(.sectionHeader)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .tracking(0.5)

                    FormTextField(
                        placeholder: "https://example.com/podcast/feed",
                        text: $newFeedURL,
                        keyboardType: .URL,
                        textInputAutocapitalization: .never,
                        autocorrectionDisabled: true,
                        accessibilityLabel: "Podcast feed URL"
                    )
                }

                // Name field
                VStack(alignment: .leading, spacing: 8) {
                    Text("DISPLAY NAME")
                        .font(.sectionHeader)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .tracking(0.5)

                    FormTextField(
                        placeholder: "Optional",
                        text: $newFeedName,
                        accessibilityLabel: "Display name"
                    )
                }

                // Limit field
                VStack(alignment: .leading, spacing: 8) {
                    Text("EPISODE LIMIT")
                        .font(.sectionHeader)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .tracking(0.5)

                    FormTextField(
                        placeholder: "1-100, optional",
                        text: $newLimit,
                        keyboardType: .numberPad,
                        accessibilityLabel: "Episode limit"
                    )
                }

                if let addSourceError {
                    addSourceErrorBanner(addSourceError)
                }

                Spacer()
            }
            .padding()
            .background(Color.surfacePrimary)
            .navigationTitle("Add Podcast Source")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        resetAddForm()
                        activeSheet = nil
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") {
                        Task { await submitAddSource() }
                    }
                    .fontWeight(.semibold)
                    .disabled(isAddingSource || newFeedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    private func addSourceErrorBanner(_ error: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(Color.statusDestructive)
                .accessibilityHidden(true)

            Text(error)
                .font(.appSubheadline)
                .foregroundStyle(Color.onSurface)

            Spacer()
        }
        .padding()
        .background(Color.statusDestructive.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
    }

    @MainActor
    private func submitAddSource() async {
        guard !isAddingSource else { return }

        addSourceError = nil
        let trimmedLimit = newLimit.trimmingCharacters(in: .whitespacesAndNewlines)
        let limitValue = Int(trimmedLimit)

        if !trimmedLimit.isEmpty && limitValue == nil {
            addSourceError = "Limit must be a number between 1 and 100"
            return
        }

        if let limitValue, !(1...100).contains(limitValue) {
            addSourceError = "Limit must be between 1 and 100"
            return
        }

        isAddingSource = true
        defer { isAddingSource = false }

        let trimmedName = newFeedName.trimmingCharacters(in: .whitespacesAndNewlines)
        let didAdd = await viewModel.addConfig(
            scraperType: "podcast_rss",
            displayName: trimmedName.isEmpty ? nil : trimmedName,
            feedURL: newFeedURL.trimmingCharacters(in: .whitespacesAndNewlines),
            limit: limitValue
        )

        guard didAdd else {
            addSourceError = viewModel.errorMessage ?? "Failed to add source"
            return
        }

        resetAddForm()
        activeSheet = nil
    }

    private func resetAddForm() {
        newFeedURL = ""
        newFeedName = ""
        newLimit = ""
        addSourceError = nil
    }
}
