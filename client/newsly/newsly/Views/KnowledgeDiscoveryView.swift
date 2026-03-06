//
//  KnowledgeDiscoveryView.swift
//  newsly
//

import SwiftUI

struct KnowledgeDiscoveryView: View {
    @ObservedObject var viewModel: DiscoveryViewModel
    let hasNewSuggestions: Bool
    @EnvironmentObject private var authViewModel: AuthenticationViewModel
    @State private var safariTarget: SafariTarget?
    @State private var selectedSuggestion: DiscoverySuggestion?
    @State private var showPersonalizeSheet = false

    private var isEmptyState: Bool {
        !viewModel.hasSuggestions
            && !viewModel.isLoading
            && viewModel.errorMessage == nil
            && !viewModel.isJobRunning
    }

    var body: some View {
        scrollContent
        .background(Color.surfacePrimary)
        .onAppear {
            Task { await viewModel.loadSuggestions() }
        }
        .refreshable {
            await viewModel.loadSuggestions(force: true)
        }
        .sheet(item: $safariTarget) { target in
            SafariView(url: target.url)
        }
        .sheet(isPresented: $showPersonalizeSheet) {
            if let userId = currentUserId {
                DiscoveryPersonalizeSheet(userId: userId) {
                    Task { await viewModel.loadSuggestions(force: true) }
                }
            }
        }
        .sheet(item: $selectedSuggestion) { suggestion in
            SuggestionDetailSheet(
                suggestion: suggestion,
                onSubscribe: {
                    Task { await viewModel.subscribe(suggestion) }
                },
                onAddItem: suggestion.hasItem ? {
                    Task { await viewModel.addItem(from: suggestion) }
                } : nil,
                onPreview: {
                    openSuggestionURL(suggestion)
                },
                onDismiss: {
                    Task { await viewModel.dismiss(suggestion) }
                }
            )
        }
    }

    // MARK: - Scroll Content

    private var scrollContent: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                // Editorial search bar + action buttons
                VStack(spacing: 8) {
                    editorialSearchBar

                    discoveryActionBar
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 4)

                // Podcast search results (inline)
                if isPodcastSearchActive {
                    podcastSearchResults
                        .padding(.horizontal, Spacing.screenHorizontal)
                        .padding(.bottom, 8)
                }

                // Main content states
                if viewModel.isLoading && !viewModel.hasSuggestions {
                    DiscoveryLoadingStateView()
                } else if let error = viewModel.errorMessage, !viewModel.hasSuggestions {
                    DiscoveryErrorStateView(error: error) {
                        Task { await viewModel.loadSuggestions(force: true) }
                    }
                } else if isEmptyState {
                    DiscoveryEmptyStateView {
                        Task { await viewModel.refreshDiscovery() }
                    }
                } else if !viewModel.hasSuggestions && viewModel.isJobRunning {
                    DiscoveryProcessingStateView(
                        runStatusDescription: viewModel.runStatusDescription,
                        currentJobStage: viewModel.currentJobStage
                    )
                } else {
                    suggestionContent
                }
            }
        }
    }

    // MARK: - Action Bar

    private var discoveryActionBar: some View {
        HStack(spacing: 12) {
            Spacer()

            Button {
                Task { await viewModel.refreshDiscovery() }
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(.textSecondary)
            }
            .accessibilityLabel("Refresh Discovery")

            Menu {
                Button(role: .destructive) {
                    Task { await viewModel.clearAll() }
                } label: {
                    Label("Clear Suggestions", systemImage: "trash")
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(.textSecondary)
            }
            .accessibilityLabel("Discovery Options")
        }
    }

    // MARK: - Search Bar

    private var editorialSearchBar: some View {
        SearchBar(
            placeholder: "Search for content...",
            text: $viewModel.podcastSearchQuery,
            isLoading: viewModel.isPodcastSearchLoading,
            onSubmit: {
                Task { await viewModel.searchPodcastEpisodes() }
            },
            onClear: {
                viewModel.clearPodcastSearch()
            }
        )
    }

    // MARK: - Podcast Search Results

    private var isPodcastSearchActive: Bool {
        viewModel.isPodcastSearchLoading
            || viewModel.podcastSearchError != nil
            || (viewModel.hasPodcastSearchRun && !viewModel.podcastSearchResults.isEmpty)
            || (viewModel.hasPodcastSearchRun && viewModel.podcastSearchResults.isEmpty && !viewModel.isPodcastSearchLoading)
    }

    private var podcastSearchResults: some View {
        VStack(alignment: .leading, spacing: 8) {
            if viewModel.isPodcastSearchLoading {
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Searching...")
                        .font(.listCaption)
                        .foregroundColor(.textSecondary)
                }
                .padding(.top, 8)
            } else if let error = viewModel.podcastSearchError {
                HStack {
                    Text(error)
                        .font(.listCaption)
                        .foregroundColor(.textSecondary)
                    Spacer()
                    Button("Retry") {
                        Task { await viewModel.retryPodcastSearch() }
                    }
                    .font(.listCaption)
                }
                .padding(.top, 8)
            } else if viewModel.hasPodcastSearchRun && viewModel.podcastSearchResults.isEmpty {
                Text("No episodes found. Try broader keywords.")
                    .font(.listCaption)
                    .foregroundColor(.textSecondary)
                    .padding(.top, 8)
            }

            if viewModel.hasPodcastSearchResults {
                VStack(spacing: 8) {
                    ForEach(viewModel.podcastSearchResults) { result in
                        PodcastEpisodeSearchCard(
                            result: result,
                            onAdd: {
                                Task { await viewModel.addPodcastEpisode(result) }
                            },
                            onOpen: {
                                openPodcastSearchResultURL(result)
                            }
                        )
                    }
                }
                .padding(.top, 4)
            }
        }
    }

    // MARK: - Suggestion Content

    private var suggestionContent: some View {
        VStack(spacing: 0) {
            if viewModel.isJobRunning {
                runningJobBanner
                    .padding(.top, 12)
                    .padding(.bottom, 8)
            }

            if hasNewSuggestions && !viewModel.isJobRunning {
                newSuggestionsBanner
                    .padding(.top, 12)
                    .padding(.bottom, 8)
            }

            ForEach(Array(displayRuns.enumerated()), id: \.element.id) { index, run in
                DiscoveryRunSection(
                    run: run,
                    isLatest: index == 0,
                    onSelect: { suggestion in
                        selectedSuggestion = suggestion
                    }
                )
            }

            if !viewModel.isJobRunning {
                generateMoreCard
                    .padding(.top, 32)
                    .padding(.horizontal, Spacing.screenHorizontal)

                if currentUserId != nil {
                    personalizeCard
                        .padding(.top, 12)
                        .padding(.bottom, 40)
                        .padding(.horizontal, Spacing.screenHorizontal)
                }
            }
        }
    }

    private var generateMoreCard: some View {
        Button {
            Task { await viewModel.refreshDiscovery() }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "sparkles")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(.textSecondary)

                Text("Generate More Suggestions")
                    .font(.listTitle)
                    .foregroundColor(.textPrimary)

                Spacer()

                Image(systemName: "arrow.right")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.textTertiary)
            }
            .padding(16)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.editorialBorder, lineWidth: 1)
            )
        }
        .buttonStyle(EditorialCardButtonStyle())
    }

    private var personalizeCard: some View {
        Button {
            showPersonalizeSheet = true
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "mic.fill")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(.textSecondary)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Personalize this discovery")
                        .font(.listTitle)
                        .foregroundColor(.textPrimary)
                    Text("Tell us your interests with voice")
                        .font(.listCaption)
                        .foregroundColor(.textSecondary)
                }

                Spacer()

                Image(systemName: "arrow.right")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.textTertiary)
            }
            .padding(16)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.editorialBorder, lineWidth: 1)
            )
        }
        .buttonStyle(EditorialCardButtonStyle())
    }

    private var currentUserId: Int? {
        if case .authenticated(let user) = authViewModel.authState {
            return user.id
        }
        return nil
    }

    private var runningJobBanner: some View {
        HStack(spacing: 8) {
            ProgressView()
                .scaleEffect(0.7)

            Text("Discovering...")
                .font(.listCaption)
                .foregroundColor(.textSecondary)

            Spacer()

            Text(viewModel.runStatusDescription)
                .font(.listCaption)
                .foregroundColor(Color.textTertiary)
        }
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.vertical, 12)
    }

    private var newSuggestionsBanner: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(Color.accentColor)
                .frame(width: 6, height: 6)

            Text("New suggestions available")
                .font(.listCaption)
                .foregroundColor(.textSecondary)

            Spacer()
        }
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.vertical, 12)
    }

    // MARK: - Helpers

    private var displayRuns: [DiscoveryRunSuggestions] {
        if !viewModel.runs.isEmpty {
            return viewModel.runs
        }
        if viewModel.feeds.isEmpty && viewModel.podcasts.isEmpty && viewModel.youtube.isEmpty {
            return []
        }
        return [
            DiscoveryRunSuggestions(
                runId: -1,
                runStatus: viewModel.runStatus ?? "completed",
                runCreatedAt: viewModel.runCreatedAt ?? "",
                directionSummary: viewModel.directionSummary,
                feeds: viewModel.feeds,
                podcasts: viewModel.podcasts,
                youtube: viewModel.youtube
            )
        ]
    }

    private func openSuggestionURL(_ suggestion: DiscoverySuggestion) {
        let candidate = suggestion.itemURL ?? suggestion.siteURL ?? suggestion.feedURL
        guard let url = URL(string: candidate) else { return }
        safariTarget = SafariTarget(url: url)
    }

    private func openPodcastSearchResultURL(_ result: DiscoveryPodcastSearchResult) {
        guard let url = URL(string: result.episodeURL) else { return }
        safariTarget = SafariTarget(url: url)
    }
}

// MARK: - Podcast Episode Card (Editorial Style)

private struct PodcastEpisodeSearchCard: View {
    let result: DiscoveryPodcastSearchResult
    let onAdd: () -> Void
    let onOpen: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Episode title as headline
            Text(result.title)
                .font(.feedHeadline)
                .foregroundColor(.textPrimary)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            // Metadata bar
            HStack(spacing: 6) {
                Image(systemName: "waveform")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(.orange)

                if let podcastTitle = result.podcastTitle, !podcastTitle.isEmpty {
                    Text(podcastTitle.uppercased())
                        .font(.feedMeta)
                        .foregroundColor(.textSecondary)
                        .tracking(0.4)
                        .lineLimit(1)
                } else {
                    Text("PODCAST")
                        .font(.feedMeta)
                        .foregroundColor(.textSecondary)
                        .tracking(0.4)
                }

                Text("\u{00B7}")
                    .font(.feedMeta)
                    .foregroundColor(.textTertiary)

                Text(result.source ?? host(from: result.episodeURL))
                    .font(.feedMeta)
                    .foregroundColor(.textTertiary)
                    .lineLimit(1)

                Spacer()

                Button(action: onAdd) {
                    Label("Add", systemImage: "plus")
                        .font(.chipLabel)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .tint(.orange)

                Button(action: onOpen) {
                    Image(systemName: "safari")
                        .font(.listCaption)
                        .foregroundColor(.textSecondary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(14)
        .background(Color.surfaceSecondary)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.editorialBorder, lineWidth: 1)
        )
        .cornerRadius(10)
    }

    private func host(from urlString: String) -> String {
        guard let url = URL(string: urlString), let host = url.host else {
            return urlString
        }
        return host.replacingOccurrences(of: "www.", with: "")
    }
}

// MARK: - Safari Target

private struct SafariTarget: Identifiable {
    let id = UUID()
    let url: URL
}
