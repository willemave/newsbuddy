//
//  PodcastSourcesView.swift
//  newsly
//

import SwiftUI

struct PodcastSourcesView: View {
    @StateObject private var viewModel = ScraperSettingsViewModel(
        filterTypes: ["podcast_rss"]
    )
    @State private var selectedConfig: ScraperConfig?
    @State private var showAddSheet = false
    @State private var newFeedURL: String = ""
    @State private var newFeedName: String = ""
    @State private var newLimit: String = ""
    @State private var localError: String?

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
                            action: { showAddSheet = true }
                        )
                        .frame(minHeight: 400)
                    } else {
                        sourcesList
                    }

                    if let error = viewModel.errorMessage ?? localError {
                        errorBanner(error)
                    }
                }
            }
            .refreshable { await viewModel.loadConfigsWithDeferredStats() }

            // Floating add button
            if !viewModel.configs.isEmpty {
                AddButton { showAddSheet = true }
                    .padding(Spacing.rowHorizontal)
            }
        }
        .background(Color.surfacePrimary)
        .navigationTitle("Podcast Sources")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.loadConfigsWithDeferredStats() }
        .sheet(item: $selectedConfig) { config in
            SourceDetailSheet(viewModel: viewModel, config: config)
        }
        .sheet(isPresented: $showAddSheet) {
            addSourceSheet
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
                .onTapGesture { selectedConfig = config }

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
        }
        .padding()
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
                        showAddSheet = false
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") {
                        let trimmedLimit = newLimit.trimmingCharacters(in: .whitespacesAndNewlines)
                        let limitValue = Int(trimmedLimit)

                        if !trimmedLimit.isEmpty && limitValue == nil {
                            localError = "Limit must be a number between 1 and 100"
                            return
                        }

                        if let limitValue, !(1...100).contains(limitValue) {
                            localError = "Limit must be between 1 and 100"
                            return
                        }

                        localError = nil
                        Task {
                            await viewModel.addConfig(
                                scraperType: "podcast_rss",
                                displayName: newFeedName.isEmpty ? nil : newFeedName,
                                feedURL: newFeedURL,
                                limit: limitValue
                            )
                            if viewModel.errorMessage == nil {
                                resetAddForm()
                                showAddSheet = false
                            }
                        }
                    }
                    .fontWeight(.semibold)
                    .disabled(newFeedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    private func resetAddForm() {
        newFeedURL = ""
        newFeedName = ""
        newLimit = ""
        localError = nil
    }
}
