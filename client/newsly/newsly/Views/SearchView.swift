//
//  SearchView.swift
//  newsly
//
//  Created by Assistant on 9/15/25.
//

import SwiftUI

struct SearchView: View {
    private let readStateCache: ReadStateCache
    @State private var viewModel: SearchViewModel
    @State private var browserDestination: BrowserDestination?

    init(
        readStateCache: ReadStateCache,
        viewModel: SearchViewModel
    ) {
        self.readStateCache = readStateCache
        self._viewModel = State(initialValue: viewModel)
    }

    var body: some View {
        List {
            searchSection

            if !viewModel.hasQuery {
                introSection
            } else {
                contentSection
                externalSectionPrompt

                if viewModel.hasSubmittedSearch || viewModel.isLoadingMixed {
                    mixedSearchErrorSection
                    feedSection
                    podcastSection
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .contentMargins(.horizontal, Spacing.appHorizontalMargin, for: .scrollContent)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .toolbarBackground(Color.surfacePrimary, for: .navigationBar)
        .navigationTitle("Search")
        .onChange(of: viewModel.searchText, initial: true) { _, searchText in
            viewModel.searchTextDidChange(to: searchText)
        }
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
                .accessibilityIdentifier("search.submit")
            }
        }
        .sheet(item: $browserDestination) { destination in
            SafariView(url: destination.url)
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
                },
                inputAccessibilityIdentifier: "search.input"
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
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            if let error = viewModel.localErrorMessage {
                SearchInlineError(
                    message: error,
                    accessibilityIdentifier: "search.error.local",
                    retry: viewModel.retrySearch
                )
            }

            if viewModel.hasLocalSearch,
               viewModel.contentResults.isEmpty,
               viewModel.localErrorMessage == nil,
               !viewModel.isLoadingLocal {
                Text("No matching content.")
                    .foregroundStyle(Color.onSurfaceSecondary)
            }

            if !viewModel.contentResults.isEmpty {
                let contentIDs = viewModel.contentResults.map(\.id)
                ForEach(viewModel.contentResults, id: \.id) { item in
                    NavigationLink(value: ContentDetailRoute(
                        summary: item,
                        allContentIds: contentIDs,
                        navigationSurface: .search
                    )) {
                        HStack(spacing: 12) {
                            Image(systemName: item.contentType == .podcast ? "waveform" : "doc.text")
                                .foregroundStyle(Color.onSurfaceSecondary)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.displayTitle)
                                    .font(.listTitle)
                                    .lineLimit(3)
                                if let summary = item.summaryDisplayText {
                                    Text(summary)
                                        .font(.listCaption)
                                        .foregroundStyle(Color.onSurfaceSecondary)
                                        .lineLimit(2)
                                }
                                HStack(spacing: 6) {
                                    if let source = item.source {
                                        Text(source)
                                            .font(.listCaption)
                                            .foregroundStyle(Color.onSurfaceSecondary)
                                    }
                                    Text(item.contentType.displayName)
                                        .font(.chipLabel)
                                        .foregroundStyle(Color.onSurfaceSecondary)
                                }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .accessibilityIdentifier("search.content.\(item.id)")
                }
            }
        }
    }

    @ViewBuilder
    private var mixedSearchErrorSection: some View {
        if let error = viewModel.mixedErrorMessage {
            Section {
                SearchInlineError(
                    message: error,
                    accessibilityIdentifier: "search.error.external",
                    retry: viewModel.submitSearch
                )
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
                            .font(.appCaption)
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
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            } else if viewModel.hasSubmittedSearch,
                      viewModel.feedResults.isEmpty,
                      viewModel.mixedErrorMessage == nil {
                Text("No feed or source matches.")
                    .foregroundStyle(Color.onSurfaceSecondary)
            } else {
                ForEach(viewModel.feedResults) { result in
                    VStack(alignment: .leading, spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(result.title)
                                .font(.listTitle.weight(.semibold))
                            Text(result.rationale ?? result.description ?? result.siteURL)
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .lineLimit(3)
                        }

                        HStack(spacing: 10) {
                            Button("Open") {
                                guard let url = URL(string: result.previewURLString) else { return }
                                browserDestination = BrowserDestination(url: url)
                            }
                            .buttonStyle(.bordered)
                            .accessibilityIdentifier("search.feed.\(result.id).open")

                            Button {
                                Task { await viewModel.subscribeToFeed(result) }
                            } label: {
                                if let completedLabel = viewModel.completedActionLabels["feed:\(result.id)"] {
                                    Label(completedLabel, systemImage: "checkmark")
                                } else if viewModel.actionInFlightIds.contains("feed:\(result.id)") {
                                    ProgressView()
                                } else {
                                    Text("Subscribe")
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(
                                result.isSubscribed
                                    || viewModel.completedActionLabels["feed:\(result.id)"] != nil
                                    || viewModel.actionInFlightIds.contains("feed:\(result.id)")
                            )
                            .accessibilityIdentifier("search.feed.\(result.id).subscribe")
                        }

                        if let actionError = viewModel.actionErrorMessages["feed:\(result.id)"] {
                            Text(actionError)
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .accessibilityIdentifier("search.feed.\(result.id).error")
                        }
                    }
                    .padding(.vertical, 4)
                    .accessibilityIdentifier("search.feed.\(result.id)")
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
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            } else if viewModel.hasSubmittedSearch,
                      viewModel.podcastResults.isEmpty,
                      viewModel.mixedErrorMessage == nil {
                Text("No podcast matches.")
                    .foregroundStyle(Color.onSurfaceSecondary)
            } else {
                ForEach(viewModel.podcastResults) { result in
                    VStack(alignment: .leading, spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(result.title)
                                .font(.listTitle.weight(.semibold))
                            Text(result.podcastTitle ?? result.source ?? result.episodeURL)
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                            if let snippet = result.snippet, !snippet.isEmpty {
                                Text(snippet)
                                    .font(.listCaption)
                                    .foregroundStyle(Color.onSurfaceSecondary)
                                    .lineLimit(3)
                            }
                        }

                        HStack(spacing: 10) {
                            Button("Open") {
                                guard let url = URL(string: result.episodeURL) else { return }
                                browserDestination = BrowserDestination(url: url)
                            }
                            .buttonStyle(.bordered)
                            .accessibilityIdentifier("search.podcast.\(result.id).open")

                            Button {
                                Task { await viewModel.addPodcastEpisode(result) }
                            } label: {
                                if let completedLabel = viewModel.completedActionLabels["episode:\(result.id)"] {
                                    Label(completedLabel, systemImage: "checkmark")
                                } else if viewModel.actionInFlightIds.contains("episode:\(result.id)") {
                                    ProgressView()
                                } else {
                                    Text("Add Item")
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(
                                viewModel.completedActionLabels["episode:\(result.id)"] != nil
                                    || viewModel.actionInFlightIds.contains("episode:\(result.id)")
                            )
                            .accessibilityIdentifier("search.podcast.\(result.id).add")

                            if result.feedURL != nil {
                                Button {
                                    Task { await viewModel.subscribeToPodcast(result) }
                                } label: {
                                    if let completedLabel = viewModel.completedActionLabels[
                                        "podcast-feed:\(result.feedURL ?? "")"
                                    ] {
                                        Label(completedLabel, systemImage: "checkmark")
                                    } else if viewModel.actionInFlightIds.contains("podcast-feed:\(result.feedURL ?? "")") {
                                        ProgressView()
                                    } else {
                                        Text("Subscribe")
                                    }
                                }
                                .buttonStyle(.bordered)
                                .disabled(
                                    viewModel.completedActionLabels[
                                        "podcast-feed:\(result.feedURL ?? "")"
                                    ] != nil
                                        || viewModel.actionInFlightIds.contains(
                                            "podcast-feed:\(result.feedURL ?? "")"
                                        )
                                )
                                .accessibilityIdentifier("search.podcast.\(result.id).subscribe")
                            }
                        }

                        if let actionError = viewModel.actionErrorMessages["episode:\(result.id)"]
                            ?? result.feedURL.flatMap({
                                viewModel.actionErrorMessages["podcast-feed:\($0)"]
                            }) {
                            Text(actionError)
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .accessibilityIdentifier("search.podcast.\(result.id).error")
                        }
                    }
                    .padding(.vertical, 4)
                    .accessibilityIdentifier("search.podcast.\(result.id)")
                }
            }
        }
    }
}

private struct SearchInlineError: View {
    let message: String
    let accessibilityIdentifier: String
    let retry: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.circle")
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityHidden(true)
            Text(message)
                .font(.listCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
            Spacer(minLength: 8)
            Button("Try Again", action: retry)
                .buttonStyle(.bordered)
        }
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}
