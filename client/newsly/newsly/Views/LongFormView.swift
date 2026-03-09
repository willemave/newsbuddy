//
//  LongFormView.swift
//  newsly
//
//  Created by Assistant on 11/4/25.
//

import SwiftUI

struct LongFormView: View {
    @ObservedObject var viewModel: LongContentListViewModel
    let onSelect: (ContentDetailRoute) -> Void

    @StateObject private var processingCountService = ProcessingCountService.shared
    @StateObject private var longFormStatsService = LongFormStatsService.shared
    @State private var showMarkAllConfirmation = false
    @State private var isProcessingBulk = false

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                if viewModel.state == .initialLoading && viewModel.currentItems().isEmpty {
                    LoadingView()
                } else if case .error(let error) = viewModel.state, viewModel.currentItems().isEmpty {
                    ErrorView(message: error.localizedDescription) {
                        viewModel.refreshTrigger.send(())
                    }
                } else {
                    if viewModel.currentItems().isEmpty {
                        longFormEmptyState
                    } else {
                        ScrollView {
                            LazyVStack(spacing: CardMetrics.cardSpacing) {
                                let items = viewModel.currentItems()
                                ForEach(Array(items.enumerated()), id: \.element.id) { index, content in
                                    NavigationLink(
                                        value: ContentDetailRoute(
                                            summary: content,
                                            allContentIds: items.map(\.id)
                                        )
                                    ) {
                                        LongFormCard(
                                            content: content,
                                            onMarkRead: {
                                                viewModel.markAsRead(content.id)
                                            },
                                            onToggleFavorite: {
                                                Task {
                                                    await viewModel.toggleFavorite(content.id)
                                                }
                                            }
                                        )
                                    }
                                    .buttonStyle(.plain)
                                    .onAppear {
                                        if content.id == items.last?.id {
                                            viewModel.loadMoreTrigger.send(())
                                        }
                                    }
                                }

                                if viewModel.state == .loadingMore {
                                    HStack {
                                        Spacer()
                                        ProgressView()
                                            .padding()
                                        Spacer()
                                    }
                                }
                            }
                            .padding(.horizontal, 20)
                            .padding(.vertical, 20)
                        }
                        .refreshable {
                            await refreshUnreadFeed()
                        }
                        .simultaneousGesture(
                            LongPressGesture(minimumDuration: 0.8).onEnded { _ in
                                if viewModel.currentItems().contains(where: { !$0.isRead }) {
                                    showMarkAllConfirmation = true
                                }
                            }
                        )
                        .confirmationDialog(
                            "Mark all long-form content as read?",
                            isPresented: $showMarkAllConfirmation
                        ) {
                            Button("Mark All as Read", role: .destructive) {
                                showMarkAllConfirmation = false
                                isProcessingBulk = true
                                Task {
                                    defer { isProcessingBulk = false }
                                    await viewModel.markAllVisibleAsRead()
                                }
                            }
                            Button("Cancel", role: .cancel) {
                                showMarkAllConfirmation = false
                            }
                        } message: {
                            Text("Long press to quickly mark every unread item in the current list as read.")
                        }
                    }
                }
            }
            .onAppear {
                Task {
                    await refreshUnreadFeed()
                }
            }

            if isProcessingBulk {
                Color.black.opacity(0.15)
                    .ignoresSafeArea()
                ProgressView("Marking content")
                    .padding(16)
                    .background(Color.surfacePrimary)
                    .cornerRadius(12)
            }
        }
        .screenContainer()
    }

    @ViewBuilder
    private var longFormEmptyState: some View {
        if processingCountService.longFormProcessingCount > 0
            && longFormStatsService.totalCount == 0
        {
            VStack(spacing: 16) {
                Spacer()
                ProgressView()
                Text("Preparing \(processingCountService.longFormProcessingCount) long-form items")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.textSecondary)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if longFormStatsService.unreadCount == 0 && longFormStatsService.totalCount > 0 {
            EmptyStateView(
                icon: "checkmark.circle",
                title: "You're All Caught Up",
                subtitle: "No unread long-form content right now"
            )
        } else {
            EmptyStateView(
                icon: "doc.richtext",
                title: "No Long-Form Content",
                subtitle: "Articles and podcasts will appear here once processed"
            )
        }
    }

    @MainActor
    private func refreshUnreadFeed() async {
        async let statsRefresh: Void = longFormStatsService.refreshStats()
        async let processingRefresh: Void = processingCountService.refreshCount()
        _ = await (statsRefresh, processingRefresh)

        let previousFilter = viewModel.currentReadFilter()
        viewModel.setReadFilter(.unread)
        if previousFilter == .unread {
            viewModel.refreshTrigger.send(())
        }
    }
}
