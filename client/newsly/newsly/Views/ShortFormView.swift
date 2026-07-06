//
//  ShortFormView.swift
//  newsly
//
//  Created by Assistant on 11/4/25.
//

import os.log
import SwiftUI

private let logger = Logger(subsystem: "com.newsly", category: "ShortFormView")

struct ShortFormView: View {
    let viewModel: ShortNewsListViewModel
    let isActive: Bool
    let onSelect: (ContentDetailRoute) -> Void
    let scrollToTopRequest: Int
    @State private var narrationPlaybackService = NarrationPlaybackService.shared
    @State private var processingCountService = ProcessingCountService.shared
    @State private var scrollReadTracker = ShortNewsScrollReadTracker()
    @State private var isScrollReadTrackingEnabled = false
    private let chatService = ChatService.shared

    @State private var showMarkAllConfirmation = false
    @State private var quickActionErrorMessage: String?
    @State private var activeQuickActionId: String?
    @State private var fastNewsAudioEpisode: AudioEpisode?
    @State private var isPreparingFastNewsAudio = false
    @State private var fastNewsAudioErrorMessage: String?
    @State private var fastNewsAudioTask: Task<Void, Never>?
    @State private var quickActionTask: Task<Void, Never>?
    @State private var markReadFeedbackTrigger = 0
    @State private var bulkMarkFeedbackTrigger = 0

    private let bottomActionScrollPadding: CGFloat = 96
    private static let topAnchorID = "shortFormTop"

    var body: some View {
        let dayGroups = viewModel.dayGroups
        let items = dayGroups.flatMap(\.items)
        let itemIds = items.map(\.id)
        let isEmpty = items.isEmpty
        let hasUnreadItems = items.contains(where: { !$0.isRead })

        ScrollViewReader { scrollProxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    Color.clear
                        .frame(height: 0)
                        .id(Self.topAnchorID)
                        .accessibilityHidden(true)

                    if case .error(let error) = viewModel.state, isEmpty {
                        ErrorView(message: error) {
                            Task { await viewModel.refresh() }
                        }
                        .padding(.top, 48)
                    } else if viewModel.state == .initialLoading, isEmpty {
                        SkeletonFeedList(kind: .shortForm)
                            .containerRelativeFrame(.vertical)
                    } else if isEmpty {
                        shortFormEmptyState
                    } else {
                        EditorialMastheadHeader(
                            title: "Fast Read"
                        )

                        ShortNewsQuickActionsSection(
                            items: items,
                            isPlayingAudio: isPlayingFastNewsAudio,
                            isPreparingAudio: isPreparingFastNewsAudio,
                            isHeaderActionInFlight: isHeaderActionInFlight,
                            audioTarget: fastNewsAudioTarget,
                            playbackService: narrationPlaybackService,
                            audioErrorMessage: fastNewsAudioErrorMessage,
                            quickActionErrorMessage: quickActionErrorMessage,
                            activeQuickActionId: activeQuickActionId,
                            onToggleAudio: handleFastNewsAudioEpisode,
                            onStartQuickAction: startQuickAction
                        )
                            .padding(.bottom, shouldShowFastNewsAudioControls ? 10 : 18)
                            .animation(
                                AppMotion.panel,
                                value: shouldShowFastNewsAudioControls
                            )

                        ForEach(Array(dayGroups.enumerated()), id: \.element.id) { groupIndex, group in
                            DayDelimiter(item: group.delimiterItem, isFirst: groupIndex == 0)
                                .equatable()
                                .transition(.opacity.combined(with: .move(edge: .top)))

                            ForEach(group.items) { item in
                                Button {
                                    let route = ContentDetailRoute(
                                        contentId: item.id,
                                        contentType: item.contentType,
                                        allContentIds: itemIds,
                                        navigationSurface: .fastNews
                                    )
                                    onSelect(route)
                                } label: {
                                    ShortNewsRow(item: item)
                                        .equatable()
                                }
                                .buttonStyle(FeedRowButtonStyle())
                                // Row-level menu so the whole row (not just the title text)
                                // responds to long-press.
                                .contextMenu {
                                    if !item.isRead {
                                        Button {
                                            markReadFeedbackTrigger += 1
                                            Task { await viewModel.markRead(ids: [item.id]) }
                                        } label: {
                                            Label("Mark as Read", systemImage: "checkmark.circle")
                                        }
                                    }

                                    Button {
                                        FeedDigDeeperAction.start(
                                            selectedText: item.displayTitle,
                                            item: item,
                                            visibleContentIds: items.prefix(15).map(\.id),
                                            surface: .shortNews
                                        )
                                    } label: {
                                        Label("Dig Deeper", systemImage: "magnifyingglass")
                                    }
                                }
                                .id(item.id)
                                .accessibilityIdentifier("short.row.\(item.id)")
                                .transition(.opacity.combined(with: .move(edge: .top)))
                            }
                        }

                        if hasUnreadItems {
                            MarkAllReadButton {
                                showMarkAllConfirmation = true
                            }
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                            .padding(.vertical, 8)
                            .transition(.opacity)
                        }

                        if viewModel.state == .loadingMore {
                            ProgressView()
                                .padding(.vertical, 16)
                        }

                        Color.clear
                            .frame(height: bottomActionScrollPadding)
                            .accessibilityHidden(true)
                    }
                }
                .scrollTargetLayout()
                .background(Color.surfacePrimary)
                .animation(AppMotion.subtle, value: hasUnreadItems)
                .animation(AppMotion.subtle, value: itemIds)
            }
            .scrollIndicators(.hidden)
            .onPaginationThresholdReached {
                await viewModel.loadNextPage()
            }
            .background(Color.surfacePrimary.ignoresSafeArea())
            .accessibilityIdentifier("short.screen")
            .screenContainer()
            .topScreenEdgeFade()
            .onScrollTargetVisibilityChange(idType: Int.self) { visibleIds in
                scrollReadTracker.updateTopVisibleItemId(visibleIds.first)
                markItemsAboveAsRead()
            }
            .onScrollPhaseChange { _, newPhase in
                guard newPhase == .idle else { return }
                markItemsAboveAsRead()
            }
            .onChange(of: scrollToTopRequest) { _, request in
                guard request > 0 else { return }
                withAnimation(AppMotion.panel) {
                    scrollProxy.scrollTo(Self.topAnchorID, anchor: .top)
                }
            }
            .task(id: isActive) {
                isScrollReadTrackingEnabled = false
                guard isActive else { return }
                guard await TabActivationTiming.waitForSettle() else { return }
                isScrollReadTrackingEnabled = true
                markItemsAboveAsRead()
            }
            .refreshable {
                // Background replace keeps the current rows visible while the fresh
                // page loads, and awaiting it keeps the spinner up until it lands.
                await viewModel.refreshInBackgroundAndWait()
                await processingCountService.refreshCount()
            }
            .onChange(of: processingCountService.newsProcessingCount) {
                refreshFeedIfAwaitingFirstItems()
            }
            .onChange(of: processingCountService.newsCrawlCount) {
                refreshFeedIfAwaitingFirstItems()
            }
            .onAppear {
                if viewModel.currentItems().isEmpty {
                    Task { await viewModel.refresh() }
                }
                Task {
                    await processingCountService.refreshCount()
                }
            }
            .onDisappear {
                fastNewsAudioTask?.cancel()
                fastNewsAudioTask = nil
                quickActionTask = nil
            }
            .alert(
                "Mark all news items as read?",
                isPresented: $showMarkAllConfirmation
            ) {
                Button("Cancel", role: .cancel) {
                    showMarkAllConfirmation = false
                }
                Button("Mark All as Read", role: .destructive) {
                    bulkMarkFeedbackTrigger += 1
                    showMarkAllConfirmation = false
                    Task { await viewModel.markAllVisibleAsRead() }
                }
            } message: {
                Text("Marks every unread item currently loaded in the list.")
            }
            .sensoryFeedback(.impact(weight: .light), trigger: markReadFeedbackTrigger)
            .sensoryFeedback(.success, trigger: bulkMarkFeedbackTrigger)
        }
    }

    private var isPlayingFastNewsAudio: Bool {
        guard let target = fastNewsAudioTarget else { return false }
        return narrationPlaybackService.isSpeaking
            && narrationPlaybackService.speakingTarget == target
    }

    private var fastNewsAudioTarget: NarrationTarget? {
        fastNewsAudioEpisode.map { .audioEpisode($0.id) }
    }

    private var isFastNewsAudioCurrent: Bool {
        guard let target = fastNewsAudioTarget else { return false }
        return narrationPlaybackService.speakingTarget == target
    }

    private var shouldShowFastNewsAudioControls: Bool {
        isPreparingFastNewsAudio || isFastNewsAudioCurrent
    }

    private var isHeaderActionInFlight: Bool {
        isPreparingFastNewsAudio || activeQuickActionId != nil
    }

    private func refreshFeedIfAwaitingFirstItems() {
        guard viewModel.currentItems().isEmpty else { return }
        guard viewModel.state != .initialLoading else { return }
        Task { await viewModel.refresh() }
    }

    private func handleFastNewsAudioEpisode() {
        if isPlayingFastNewsAudio {
            narrationPlaybackService.pause()
            return
        }
        guard !isPreparingFastNewsAudio else { return }

        isPreparingFastNewsAudio = true
        fastNewsAudioErrorMessage = nil

        fastNewsAudioTask?.cancel()
        fastNewsAudioTask = Task { @MainActor in
            let startedAt = Date()
            logger.info("[FastNewsAudio] flow started")
            defer { isPreparingFastNewsAudio = false }

            do {
                let episode: AudioEpisode
                if let existingEpisode = fastNewsAudioEpisode {
                    episode = existingEpisode
                    logger.info(
                        "[FastNewsAudio] reusing episode | episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public)"
                    )
                } else {
                    episode = try await AudioEpisodeService.shared.createFastNewsEpisode(
                        delivery: .inline
                    )
                    logger.info(
                        "[FastNewsAudio] episode created | episodeId=\(episode.id) status=\(episode.status.rawValue, privacy: .public) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
                    )
                }
                fastNewsAudioEpisode = episode
                let target = NarrationTarget.audioEpisode(episode.id)
                try await narrationPlaybackService.playStreamingNarration(
                    for: target,
                    fetchStreamResource: {
                        try await AudioEpisodeService.shared.streamResource(for: episode)
                    }
                )
                logger.info(
                    "[FastNewsAudio] playback requested | episodeId=\(episode.id) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
                )
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                logger.error(
                    "[FastNewsAudio] flow failed | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
                )
                fastNewsAudioErrorMessage = error.localizedDescription
            }
        }
    }

    private func markItemsAboveAsRead() {
        guard isScrollReadTrackingEnabled else { return }
        let items = viewModel.currentItems()
        let idsToMark = scrollReadTracker.idsToMarkAboveTop(in: items)
        guard !idsToMark.isEmpty else { return }

        logger.info("[ShortFormView] Items scrolled past top | ids=\(idsToMark, privacy: .public)")
        viewModel.itemsScrolledPastTop(ids: idsToMark)
    }

    private func startQuickAction(_ action: ShortNewsQuickAction) {
        guard activeQuickActionId == nil else { return }

        activeQuickActionId = action.id
        quickActionErrorMessage = nil

        quickActionTask?.cancel()
        quickActionTask = Task { @MainActor in
            defer { activeQuickActionId = nil }

            do {
                let response = try await chatService.createAssistantTurn(
                    message: action.prompt,
                    screenContext: action.screenContext
                )
                ChatNavigationCoordinator.shared.openAssistantTurn(response)
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                quickActionErrorMessage = error.localizedDescription
            }
        }
    }

    @ViewBuilder
    private var shortFormEmptyState: some View {
        if processingCountService.newsProcessingCount > 0 || processingCountService.newsCrawlCount > 0 {
            ShortFormSetupEmptyState(
                processingCount: processingCountService.newsProcessingCount,
                crawlingSourceCount: processingCountService.newsCrawlCount
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .containerRelativeFrame(.vertical)
        } else {
            EmptyStateView(
                icon: "bolt.fill",
                title: "No Fast Reads Yet",
                subtitle: "Fresh items from your selected news sources will appear here once processed"
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .containerRelativeFrame(.vertical)
        }
    }
}
