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
                            LazyVStack(spacing: 0) {
                                // Editorial header
                                Text("Longread")
                                    .font(.terracottaDisplayLarge)
                                    .foregroundStyle(Color.onSurface)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.horizontal, Spacing.screenHorizontal)
                                    .padding(.top, 16)
                                    .padding(.bottom, 24)

                                VStack(spacing: CardMetrics.cardSpacing) {
                                    let items = viewModel.currentItems()
                                    let groups = bentoGroups(from: items)
                                    ForEach(groups.indices, id: \.self) { groupIndex in
                                        let group = groups[groupIndex]
                                        bentoGroupView(group: group, allItems: items)
                                    }
                                }
                                .padding(.horizontal, Spacing.screenHorizontal)

                                if viewModel.state == .loadingMore {
                                    HStack {
                                        Spacer()
                                        ProgressView()
                                            .padding()
                                        Spacer()
                                    }
                                }
                            }
                            .padding(.vertical, 12)
                        }
                        .refreshable {
                            await refreshLongFormSurface(forceReload: true)
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
                    await refreshLongFormSurface(forceReload: false)
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

    // MARK: - Bento Grid Layout

    private struct BentoGroup {
        enum Layout {
            case hero(ContentSummary)
            case pair(ContentSummary, ContentSummary)
            case single(ContentSummary)
        }
        let layout: Layout
    }

    private func bentoGroups(from items: [ContentSummary]) -> [BentoGroup] {
        var groups: [BentoGroup] = []
        var index = 0
        while index < items.count {
            // Hero card
            groups.append(BentoGroup(layout: .hero(items[index])))
            index += 1
            // Side pair (if two more items available)
            if index + 1 < items.count {
                groups.append(BentoGroup(layout: .pair(items[index], items[index + 1])))
                index += 2
            } else if index < items.count {
                groups.append(BentoGroup(layout: .single(items[index])))
                index += 1
            }
        }
        return groups
    }

    @ViewBuilder
    private func bentoGroupView(group: BentoGroup, allItems: [ContentSummary]) -> some View {
        switch group.layout {
        case .hero(let content):
            cardLink(content: content, variant: .hero, allItems: allItems)

        case .pair(let left, let right):
            HStack(spacing: 12) {
                cardLink(content: left, variant: .compact, allItems: allItems)
                cardLink(content: right, variant: .compact, allItems: allItems)
            }

        case .single(let content):
            cardLink(content: content, variant: .compact, allItems: allItems)
        }
    }

    @ViewBuilder
    private func cardLink(content: ContentSummary, variant: LongFormCard.Variant, allItems: [ContentSummary]) -> some View {
        NavigationLink(
            value: ContentDetailRoute(
                summary: content,
                allContentIds: allItems.map(\.id)
            )
        ) {
            LongFormCard(
                content: content,
                variant: variant,
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
            if content.id == allItems.last?.id {
                viewModel.loadMoreTrigger.send(())
            }
        }
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
    private func refreshLongFormSurface(forceReload: Bool) async {
        async let statsRefresh: Void = longFormStatsService.refreshStats()
        async let processingRefresh: Void = processingCountService.refreshCount()
        _ = await (statsRefresh, processingRefresh)

        if forceReload {
            viewModel.refreshUnreadFeed()
        } else {
            viewModel.ensureUnreadFeedLoaded()
        }
    }
}
