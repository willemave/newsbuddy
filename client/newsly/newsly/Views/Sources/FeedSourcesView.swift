//
//  FeedSourcesView.swift
//  newsly
//

import SwiftUI

struct FeedSourcesView: View {
    @StateObject private var viewModel = ScraperSettingsViewModel(
        filterTypes: ["substack", "atom", "youtube"]
    )
    @State private var selectedConfig: ScraperConfig?
    @State private var showAddSheet = false
    @State private var newFeedURL: String = ""
    @State private var newFeedName: String = ""
    @State private var newFeedType: String = "substack"
    private let feedTypeOptions = [
        FormChoiceOption(title: "Substack", value: "substack"),
        FormChoiceOption(title: "RSS/Atom", value: "atom"),
        FormChoiceOption(title: "YouTube", value: "youtube"),
    ]

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            ScrollView {
                LazyVStack(spacing: 0) {
                    if viewModel.isLoading && viewModel.configs.isEmpty {
                        loadingView
                    } else if viewModel.configs.isEmpty {
                        SettingsEmptyStateView(
                            icon: "antenna.radiowaves.left.and.right",
                            title: "No Feed Sources",
                            subtitle: "Add RSS feeds, Substacks, or YouTube channels to start receiving content",
                            actionTitle: "Add Source",
                            action: { showAddSheet = true }
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
                AddButton { showAddSheet = true }
                    .padding(Spacing.rowHorizontal)
            }
        }
        .background(Color.surfacePrimary)
        .navigationTitle("Feed Sources")
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
                .font(.subheadline)
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
                    name: config.displayName ?? config.feedURL ?? "Feed",
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
                .font(.caption)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .padding()
    }

    // MARK: - Add Source Sheet

    private var addSourceSheet: some View {
        NavigationStack {
            VStack(spacing: 24) {
                // Type selector
                VStack(alignment: .leading, spacing: 8) {
                    Text("TYPE")
                        .font(.sectionHeader)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .tracking(0.5)

                    FormChoicePillGroup(options: feedTypeOptions, selection: $newFeedType)
                }

                // URL field
                VStack(alignment: .leading, spacing: 8) {
                    Text("FEED URL")
                        .font(.sectionHeader)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .tracking(0.5)

                    FormTextField(
                        placeholder: "https://example.com/feed",
                        text: $newFeedURL,
                        keyboardType: .URL,
                        textInputAutocapitalization: .never,
                        autocorrectionDisabled: true,
                        accessibilityLabel: "Feed URL"
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

                Spacer()
            }
            .padding()
            .background(Color.surfacePrimary)
            .navigationTitle("Add Feed Source")
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
                        Task {
                            await viewModel.addConfig(
                                scraperType: newFeedType,
                                displayName: newFeedName.isEmpty ? nil : newFeedName,
                                feedURL: newFeedURL
                            )
                            resetAddForm()
                            showAddSheet = false
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
        newFeedType = "substack"
    }

}
