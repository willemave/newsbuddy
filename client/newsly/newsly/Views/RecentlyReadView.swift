//
//  RecentlyReadView.swift
//  newsly
//
//  Created by Assistant on 9/29/25.
//

import SwiftUI

struct RecentlyReadView: View {
    @StateObject private var viewModel = ContentListViewModel()
    @ObservedObject private var settings = AppSettings.shared
    @State private var showingFilters = false

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
                    List {
                        ForEach(viewModel.contents) { content in
                            NavigationLink(destination: ContentDetailView(
                                    contentId: content.id,
                                    allContentIds: viewModel.contents.map(\.id)
                                )) {
                                ContentCard(content: content)
                            }
                            .buttonStyle(.plain)
                            .appListRow()
                            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                Button {
                                    Task {
                                        try? await ContentService.shared.markContentAsUnread(id: content.id)
                                        withAnimation(.easeOut(duration: 0.3)) {
                                            viewModel.contents.removeAll { $0.id == content.id }
                                        }
                                    }
                                } label: {
                                    Label("Mark as Unread", systemImage: "circle")
                                }
                                .tint(.orange)
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button {
                                    Task {
                                        await viewModel.toggleFavorite(content.id)
                                    }
                                } label: {
                                    Label(
                                        content.isFavorited ? "Remove from Favorites" : "Add to Favorites",
                                        systemImage: content.isFavorited ? "star.slash" : "star"
                                    )
                                }
                                .tint(content.isFavorited ? .red : .yellow)
                            }
                            .onAppear {
                                if content.id == viewModel.contents.last?.id {
                                    Task { await viewModel.loadMoreContent() }
                                }
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
                    availableDates: viewModel.availableDates
                )
                .onDisappear {
                    Task { await viewModel.loadRecentlyRead() }
                }
            }
        }
        .navigationTitle("Recently Read")
    }
}

#Preview {
    RecentlyReadView()
}
