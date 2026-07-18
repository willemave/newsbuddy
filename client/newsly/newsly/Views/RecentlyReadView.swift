//
//  RecentlyReadView.swift
//  newsly
//
//  Created by Assistant on 9/29/25.
//

import SwiftUI

struct RecentlyReadView: View {
    let readStateCache: ReadStateCache

    @State private var viewModel: ContentListViewModel
    @State private var showingFilters = false

    init(readStateCache: ReadStateCache? = nil) {
        let readStateCache = readStateCache ?? ReadStateCache()
        self.readStateCache = readStateCache
        self._viewModel = State(
            initialValue: RootDependencyFactory.makeContentListViewModel(
                readStateCache: readStateCache
            )
        )
    }

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                if viewModel.isLoading && viewModel.contents.isEmpty {
                    LoadingView()
                } else if let error = viewModel.errorMessage, viewModel.contents.isEmpty {
                    ErrorView(message: error) {
                        Task { await viewModel.loadRecentlyRead() }
                    }
                } else if viewModel.contents.isEmpty {
                    EmptyStateView(
                        icon: "clock.badge.questionmark",
                        title: "No Recently Read Items",
                        subtitle: "Items you've read will appear here, sorted by most recently read."
                    )
                } else {
                    let contentIds = viewModel.contents.map(\.id)

                    List {
                        ForEach(viewModel.contents) { content in
                            NavigationLink(value: ContentDetailRoute(
                                summary: content,
                                allContentIds: contentIds,
                                navigationSurface: .recentlyRead
                            )) {
                                ContentCard(content: content)
                            }
                            .buttonStyle(.plain)
                            .appListRow()
                            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                Button {
                                    Task {
                                        await viewModel.markAsUnreadAndRemove(content.id)
                                    }
                                } label: {
                                    Label("Mark as Unread", systemImage: "circle")
                                }
                                .tint(Color.brandPrimary)
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button {
                                    Task {
                                        await viewModel.toggleKnowledgeSave(content.id)
                                    }
                                } label: {
                                    Label(
                                        content.isSavedToKnowledge ? "Remove from Knowledge" : "Save to Knowledge",
                                        systemImage: content.isSavedToKnowledge ? "books.vertical.fill" : "books.vertical"
                                    )
                                }
                                .tint(Color.brandPrimary)
                            }
                        }

                        if viewModel.isLoadingMore {
                            HStack {
                                Spacer()
                                ProgressView()
                                    .padding()
                                Spacer()
                            }
                            .appListRow()
                        }
                    }
                    .listStyle(.plain)
                    .onPaginationThresholdReached {
                        await viewModel.loadMoreContent()
                    }
                    .refreshable {
                        await viewModel.loadRecentlyRead()
                    }
                }
            }
            .task {
                await viewModel.loadRecentlyRead()
            }
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showingFilters = true
                    } label: {
                        Image(systemName: "line.3.horizontal.decrease.circle")
                    }
                    .accessibilityLabel("Filters")
                }
            }
            .sheet(isPresented: $showingFilters) {
                FilterSheet(
                    selectedContentType: $viewModel.selectedContentType,
                    selectedDate: $viewModel.selectedDate,
                    selectedReadFilter: $viewModel.selectedReadFilter,
                    contentTypes: viewModel.contentTypes,
                    availableDates: viewModel.availableDates,
                    showsReadStatus: false
                )
            }
        }
        .appNavigationTitle("Recently Read")
    }
}

#Preview {
    RecentlyReadView()
}
