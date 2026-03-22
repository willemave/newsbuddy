//
//  SearchView.swift
//  newsly
//
//  Created by Assistant on 9/15/25.
//

import SwiftUI

struct SearchView: View {
    @StateObject private var viewModel = SearchViewModel()
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            searchSection

            if !viewModel.hasQuery {
                introSection
            } else if let error = viewModel.errorMessage,
                      !viewModel.isLoadingLocal,
                      !viewModel.isLoadingMixed,
                      viewModel.contentResults.isEmpty,
                      viewModel.feedResults.isEmpty,
                      viewModel.podcastResults.isEmpty {
                Section {
                    ErrorView(message: error) {
                        viewModel.retrySearch()
                    }
                }
            } else {
                contentSection
                externalSectionPrompt

                if viewModel.hasSubmittedSearch || viewModel.isLoadingMixed {
                    feedSection
                    podcastSection
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Search")
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    viewModel.submitSearch()
                } label: {
                    if viewModel.isLoadingMixed {
                        ProgressView()
                    } else {
                        Text("Search")
                    }
                }
                .disabled(!viewModel.hasQuery || viewModel.isLoadingMixed)
            }
        }
    }

    private var searchSection: some View {
        Section {
            SearchBar(
                placeholder: "Search content, feeds, and podcasts",
                text: $viewModel.searchText,
                isLoading: viewModel.isLoadingLocal || viewModel.isLoadingMixed,
                onSubmit: {
                    viewModel.submitSearch()
                }
            )
            .listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
            .listRowBackground(Color.clear)
        }
    }

    private var introSection: some View {
        Section {
            EmptyStateView(
                icon: "magnifyingglass",
                title: "Search Knowledge",
                subtitle: "Type at least 2 characters for local content. Press Search to also look for feeds, sources, and podcast episodes."
            )
            .listRowInsets(EdgeInsets())
            .listRowBackground(Color.clear)
        }
    }

    private var contentSection: some View {
        Section("Content") {
            if viewModel.isLoadingLocal && viewModel.contentResults.isEmpty {
                HStack {
                    ProgressView()
                    Text("Searching your content...")
                        .foregroundStyle(Color.textSecondary)
                }
            } else if viewModel.hasLocalSearch && viewModel.contentResults.isEmpty {
                Text("No matching content.")
                    .foregroundStyle(Color.textSecondary)
            } else {
                ForEach(viewModel.contentResults, id: \.id) { item in
                    NavigationLink(destination: ContentDetailView(contentId: item.id)) {
                        HStack(spacing: 12) {
                            Image(systemName: item.apiContentType == .podcast ? "waveform" : "doc.text")
                                .foregroundStyle(Color.textSecondary)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.displayTitle)
                                    .font(.listTitle)
                                    .lineLimit(3)
                                if let summary = item.shortSummary, !summary.isEmpty {
                                    Text(summary)
                                        .font(.listCaption)
                                        .foregroundStyle(Color.textSecondary)
                                        .lineLimit(2)
                                }
                                HStack(spacing: 6) {
                                    if let source = item.source {
                                        Text(source)
                                            .font(.listCaption)
                                            .foregroundStyle(Color.textTertiary)
                                    }
                                    Text(item.contentType.capitalized)
                                        .font(.chipLabel)
                                        .foregroundStyle(Color.textTertiary)
                                }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var externalSectionPrompt: some View {
        if viewModel.hasQuery && !viewModel.hasSubmittedSearch && !viewModel.isLoadingMixed {
            Section {
                Button {
                    viewModel.submitSearch()
                } label: {
                    HStack {
                        Image(systemName: "dot.radiowaves.left.and.right")
                        Text("Search feeds, sources, and podcasts")
                        Spacer()
                        Image(systemName: "arrow.right")
                            .font(.caption)
                    }
                }
            }
        }
    }

    private var feedSection: some View {
        Section("Feeds & Sources") {
            if viewModel.isLoadingMixed && viewModel.feedResults.isEmpty {
                HStack {
                    ProgressView()
                    Text("Finding subscribable sources...")
                        .foregroundStyle(Color.textSecondary)
                }
            } else if viewModel.hasSubmittedSearch && viewModel.feedResults.isEmpty {
                Text("No feed or source matches.")
                    .foregroundStyle(Color.textSecondary)
            } else {
                ForEach(viewModel.feedResults) { result in
                    VStack(alignment: .leading, spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(result.title)
                                .font(.listTitle.weight(.semibold))
                            Text(result.rationale ?? result.description ?? result.siteURL)
                                .font(.listCaption)
                                .foregroundStyle(Color.textSecondary)
                                .lineLimit(3)
                        }

                        HStack(spacing: 10) {
                            Button("Open") {
                                guard let url = URL(string: result.previewURLString) else { return }
                                openURL(url)
                            }
                            .buttonStyle(.bordered)

                            Button {
                                Task { await viewModel.subscribeToFeed(result) }
                            } label: {
                                if viewModel.completedActionIds.contains("feed:\(result.id)") {
                                    Label("Subscribed", systemImage: "checkmark")
                                } else if viewModel.actionInFlightIds.contains("feed:\(result.id)") {
                                    ProgressView()
                                } else {
                                    Text("Subscribe")
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(viewModel.actionInFlightIds.contains("feed:\(result.id)"))
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private var podcastSection: some View {
        Section("Podcasts") {
            if viewModel.isLoadingMixed && viewModel.podcastResults.isEmpty {
                HStack {
                    ProgressView()
                    Text("Searching podcast episodes...")
                        .foregroundStyle(Color.textSecondary)
                }
            } else if viewModel.hasSubmittedSearch && viewModel.podcastResults.isEmpty {
                Text("No podcast matches.")
                    .foregroundStyle(Color.textSecondary)
            } else {
                ForEach(viewModel.podcastResults) { result in
                    VStack(alignment: .leading, spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(result.title)
                                .font(.listTitle.weight(.semibold))
                            Text(result.podcastTitle ?? result.source ?? result.episodeURL)
                                .font(.listCaption)
                                .foregroundStyle(Color.textSecondary)
                            if let snippet = result.snippet, !snippet.isEmpty {
                                Text(snippet)
                                    .font(.listCaption)
                                    .foregroundStyle(Color.textSecondary)
                                    .lineLimit(3)
                            }
                        }

                        HStack(spacing: 10) {
                            Button("Open") {
                                guard let url = URL(string: result.episodeURL) else { return }
                                openURL(url)
                            }
                            .buttonStyle(.bordered)

                            Button {
                                Task { await viewModel.addPodcastEpisode(result) }
                            } label: {
                                if viewModel.completedActionIds.contains("episode:\(result.id)") {
                                    Label("Added", systemImage: "checkmark")
                                } else if viewModel.actionInFlightIds.contains("episode:\(result.id)") {
                                    ProgressView()
                                } else {
                                    Text("Add Item")
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(viewModel.actionInFlightIds.contains("episode:\(result.id)"))

                            if result.feedURL != nil {
                                Button {
                                    Task { await viewModel.subscribeToPodcast(result) }
                                } label: {
                                    if viewModel.completedActionIds.contains("podcast-feed:\(result.feedURL ?? "")") {
                                        Label("Subscribed", systemImage: "checkmark")
                                    } else if viewModel.actionInFlightIds.contains("podcast-feed:\(result.feedURL ?? "")") {
                                        ProgressView()
                                    } else {
                                        Text("Subscribe")
                                    }
                                }
                                .buttonStyle(.bordered)
                                .disabled(
                                    viewModel.actionInFlightIds.contains("podcast-feed:\(result.feedURL ?? "")")
                                )
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }
}
