//
//  ContentListView.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

struct ContentListView: View {
    @StateObject private var viewModel = ContentListViewModel()
    @State private var showingFilters = false
    @State private var navigationPath = NavigationPath()
    
    var body: some View {
        NavigationStack(path: $navigationPath) {
            ZStack {
                VStack(spacing: 0) {
                    if viewModel.isLoading && viewModel.contents.isEmpty {
                        LoadingView()
                    } else if let error = viewModel.errorMessage, viewModel.contents.isEmpty {
                        ErrorView(message: error) {
                            Task { await viewModel.loadContent() }
                        }
                    } else {
                        // Content List
                        if viewModel.contents.isEmpty {
                            VStack(spacing: 16) {
                                Spacer()
                                Image(systemName: "doc.text.magnifyingglass")
                                    .font(.largeTitle)
                                    .foregroundColor(Color.onSurfaceSecondary)
                                Text("No content found matching your filters.")
                                    .foregroundColor(Color.onSurfaceSecondary)
                                Spacer()
                            }
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                        } else {
                            List {
                                let allContentIds = viewModel.contents.map(\.id)
                                ForEach(viewModel.contents) { content in
                                    NavigationLink(
                                        value: ContentDetailRoute(
                                            summary: content,
                                            allContentIds: allContentIds
                                        )
                                    ) {
                                        ContentCard(content: content)
                                    }
                                    .buttonStyle(.plain)
                                    .contentShape(Rectangle())
                                    .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
                                    .listRowSeparator(.visible)
                                    .listRowBackground(Color.clear)
                                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                        if !content.isRead {
                                            Button {
                                                Task {
                                                    await viewModel.markAsRead(content.id)
                                                }
                                            } label: {
                                                Label("Mark as Read", systemImage: "checkmark.circle.fill")
                                            }
                                            .tint(Color.brandPrimary)
                                        }
                                    }
                                }
                            }
                            .listStyle(.plain)
                            .padding(.top, 20)
                            .refreshable {
                                await viewModel.refresh()
                            }
                        }
                    }
                }
                .navigationBarHidden(true)
                .task {
                    await viewModel.loadContent()
                }
                .navigationDestination(for: ContentDetailRoute.self) { route in
                    ContentDetailView(
                        contentId: route.contentId,
                        contentType: route.contentType,
                        allContentIds: route.allContentIds
                    )
                }
                
                // Floating menu button
                VStack {
                    HStack {
                        Button(action: {
                            showingFilters.toggle()
                        }) {
                            Image(systemName: "line.3.horizontal.decrease.circle.fill")
                                .font(.system(size: 44))
                                .foregroundColor(.brandPrimary)
                                .background(Circle().fill(Color.surfaceSecondary))
                                .shadow(radius: 4)
                        }
                        .padding()
                        Spacer()
                    }
                    Spacer()
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
            }
        }
    }
}

struct ContentListView_Previews: PreviewProvider {
    static var previews: some View {
        ContentListView()
    }
}
