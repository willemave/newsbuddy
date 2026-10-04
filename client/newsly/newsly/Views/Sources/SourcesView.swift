//
//  SourcesView.swift
//  newsly
//

import SwiftUI

enum SourceListKind {
    case feed
    case podcast

    var title: String { self == .feed ? "Feed Sources" : "Podcast Sources" }
    var prefix: String { self == .feed ? "feed_sources" : "podcast_sources" }
    var icon: String { self == .feed ? "antenna.radiowaves.left.and.right" : "waveform" }
    var subtitle: String {
        self == .feed
            ? "Add RSS feeds, Substacks, or YouTube channels to start receiving content"
            : "Add podcast RSS feeds to start receiving episodes"
    }
}

private enum SourcesSheetDestination: Identifiable {
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

struct SourcesView: View {
    private let kind: SourceListKind
    @State private var newLimit = ""
    @State private var viewModel: ScraperSettingsViewModel
    @State private var activeSheet: SourcesSheetDestination?
    @State private var newFeedURL: String = ""
    @State private var newFeedName: String = ""
    @State private var newFeedType: String = APIScraperType.substack.rawValue
    @State private var addSourceError: String?
    @State private var isAddingSource = false
    private let feedTypeOptions = [
        FormChoiceOption(title: "Substack", value: APIScraperType.substack.rawValue),
        FormChoiceOption(title: "RSS/Atom", value: APIScraperType.atom.rawValue),
        FormChoiceOption(title: "YouTube", value: APIScraperType.youtube.rawValue),
    ]

    init(kind: SourceListKind, viewModel: ScraperSettingsViewModel) {
        self.kind = kind
        self._viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            ScrollView {
                LazyVStack(spacing: 0) {
                    if viewModel.isLoading && viewModel.configs.isEmpty {
                        loadingView
                    } else if viewModel.configs.isEmpty {
                        EmptyStateView(
                            icon: kind.icon,
                            title: "No \(kind.title)",
                            subtitle: kind.subtitle,
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
        .navigationTitle(kind.title)
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
                    name: config.displayName ?? config.feedURL ?? (kind == .feed ? "Feed" : "Podcast"),
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
            .accessibilityIdentifier("\(kind.prefix).error.retry")
        }
        .padding()
        .accessibilityIdentifier("\(kind.prefix).error")
    }

    // MARK: - Add Source Sheet

    private var addSourceSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    if kind == .feed {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("TYPE")
                                .font(.modalCaption.weight(.semibold))
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .tracking(0.5)

                            FormChoicePillGroup(
                                options: feedTypeOptions,
                                selection: $newFeedType,
                                font: .modalSupporting
                            )
                        }
                    }

                    // URL field
                    VStack(alignment: .leading, spacing: 8) {
                        Text("FEED URL")
                            .font(.modalCaption.weight(.semibold))
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .tracking(0.5)

                        FormTextField(
                            placeholder: kind == .feed ? "https://example.com/feed" : "https://example.com/podcast/feed",
                            text: $newFeedURL,
                            keyboardType: .URL,
                            textInputAutocapitalization: .never,
                            autocorrectionDisabled: true,
                            accessibilityLabel: kind == .feed ? "Feed URL" : "Podcast feed URL"
                        )
                        .font(.modalBody)
                    }

                    // Name field
                    VStack(alignment: .leading, spacing: 8) {
                        Text("DISPLAY NAME")
                            .font(.modalCaption.weight(.semibold))
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .tracking(0.5)

                        FormTextField(
                            placeholder: "Optional",
                            text: $newFeedName,
                            accessibilityLabel: "Display name"
                        )
                        .font(.modalBody)
                    }

                    if kind == .podcast {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("EPISODE LIMIT")
                                .font(.modalCaption.weight(.semibold))
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .tracking(0.5)
                            FormTextField(
                                placeholder: "1-100, optional", text: $newLimit,
                                keyboardType: .numberPad, accessibilityLabel: "Episode limit"
                            )
                            .font(.modalBody)
                        }
                    }

                    if let addSourceError {
                        addSourceErrorBanner(addSourceError)
                    }

                    Spacer(minLength: 24)
                }
                .padding()
            }
            .background(Color.surfacePrimary)
            .navigationTitle(kind == .feed ? "Add Feed Source" : "Add Podcast Source")
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
                .font(.modalSupporting)
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
        if kind == .podcast && !trimmedLimit.isEmpty && limitValue == nil {
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
            scraperType: kind == .feed ? newFeedType : APIScraperType.podcast_rss.rawValue,
            displayName: trimmedName.isEmpty ? nil : trimmedName,
            feedURL: newFeedURL.trimmingCharacters(in: .whitespacesAndNewlines),
            limit: kind == .podcast ? limitValue : nil
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
        newFeedType = APIScraperType.substack.rawValue
        newLimit = ""
        addSourceError = nil
    }

}
