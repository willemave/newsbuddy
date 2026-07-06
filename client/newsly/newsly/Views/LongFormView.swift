//
//  LongFormView.swift
//  newsly
//
//  Created by Assistant on 11/4/25.
//

import SwiftUI

struct LongFormView: View {
    let viewModel: LongContentListViewModel
    let isActive: Bool
    let onSelect: (ContentDetailRoute) -> Void
    let scrollToTopRequest: Int
    let contentTransitionNamespace: Namespace.ID?
    let onShowNarrations: () -> Void
    let currentFastReadItems: () -> [ContentSummary]

    @State private var customNarrationCreator = RootDependencyFactory.makeCustomNarrationCreationViewModel()
    @State private var sourcesViewModel = RootDependencyFactory.makeScraperSettingsViewModel(
        filterTypes: ["substack", "atom", "youtube", "podcast_rss"]
    )
    @State private var audioController = LongFormAudioController()
    @State private var unreadCountService = UnreadCountService.shared
    @State private var showMarkAllConfirmation = false
    @State private var isProcessingBulk = false
    @State private var hasLoadedBootstrapSources = false
    @State private var showCustomNarrationPicker = false
    @State private var isStartingLongFormSummaryChat = false
    @State private var longFormSummaryError: String?
    @State private var bulkMarkFeedbackTrigger = 0
    private let chatService = ChatService.shared
    private let bottomActionScrollPadding: CGFloat = 96
    private static let topAnchorID = "longFormTop"
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        let items = viewModel.currentItems()
        let itemIds = items.map(\.id)
        let lastItemId = items.last?.id
        let hasUnreadItems = items.contains(where: { !$0.isRead })

        ZStack {
            VStack(spacing: 0) {
                if viewModel.state == .initialLoading && items.isEmpty {
                    SkeletonFeedList(kind: .longForm)
                } else if case .error(let error) = viewModel.state, items.isEmpty {
                    ErrorView(message: error) {
                        Task { await viewModel.refresh() }
                    }
                } else {
                    if items.isEmpty {
                        longFormEmptyState
                    } else {
                        ScrollViewReader { scrollProxy in
                            ScrollView {
                                LazyVStack(spacing: 0) {
                                    Color.clear
                                        .frame(height: 0)
                                        .id(Self.topAnchorID)
                                        .accessibilityHidden(true)

                                EditorialMastheadHeader(title: "Long Read")

                                LongFormActionsView(
                                    isCustomNarrationGenerating: customNarrationCreator.isGenerating,
                                    customNarrationError: customNarrationCreator.errorMessage,
                                    isStartingSummaryChat: isStartingLongFormSummaryChat,
                                    summaryError: longFormSummaryError,
                                    onCreateNarration: {
                                        customNarrationCreator.errorMessage = nil
                                        showCustomNarrationPicker = true
                                    },
                                    onShowNarrations: onShowNarrations,
                                    onSummarizeRecent: {
                                        startLongFormSummaryChat(items: items)
                                    }
                                )
                                    .padding(.bottom, 14)

                                // Cards must be direct LazyVStack children: wrapping them in a
                                // plain VStack builds every card (and starts every image load)
                                // eagerly and fires the last card's load-more trigger on appear.
                                ForEach(items) { content in
                                    cardLink(content: content)
                                        .padding(.horizontal, Spacing.appHorizontalMargin)
                                        .padding(.bottom, content.id == lastItemId ? 0 : CardMetrics.cardSpacing)
                                        .transition(.opacity.combined(with: .move(edge: .top)))
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
                                    HStack {
                                        Spacer()
                                        ProgressView()
                                            .padding()
                                        Spacer()
                                    }
                                }

                                Color.clear
                                    .frame(height: bottomActionScrollPadding)
                                    .accessibilityHidden(true)
                                }
                                .animation(AppMotion.subtle, value: hasUnreadItems)
                                .animation(AppMotion.subtle, value: itemIds)
                            }
                            .onPaginationThresholdReached {
                                await viewModel.loadNextPage()
                            }
                            .onChange(of: scrollToTopRequest) { _, request in
                                guard request > 0 else { return }
                                withAnimation(AppMotion.panel) {
                                    scrollProxy.scrollTo(Self.topAnchorID, anchor: .top)
                                }
                            }
                            .refreshable {
                                await refreshLongFormSurface(forceReload: true)
                            }
                            .alert(
                                "Mark all long-form content as read?",
                                isPresented: $showMarkAllConfirmation
                            ) {
                                Button("Cancel", role: .cancel) {
                                    showMarkAllConfirmation = false
                                }
                                Button("Mark All as Read", role: .destructive) {
                                    bulkMarkFeedbackTrigger += 1
                                    showMarkAllConfirmation = false
                                    isProcessingBulk = true
                                    Task {
                                        defer { isProcessingBulk = false }
                                        await viewModel.markAllVisibleAsRead()
                                    }
                                }
                            } message: {
                                Text("Marks every unread long-form item currently loaded in the list.")
                            }
                        }
                    }
                }
            }
            .task(id: shouldPollLongForm) {
                guard shouldPollLongForm else { return }
                await runLongFormPollingLoop()
            }

            if isProcessingBulk {
                Color.black.opacity(0.15)
                    .ignoresSafeArea()
                ProgressView("Marking content")
                    .padding(16)
                    .background(Color.surfacePrimary)
                    .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
            }
        }
        .screenContainer()
        .topScreenEdgeFade()
        .accessibilityIdentifier("long.screen")
        .sensoryFeedback(.success, trigger: bulkMarkFeedbackTrigger)
        .sheet(isPresented: $showCustomNarrationPicker) {
            CustomNarrationPickerSheet(
                currentItems: viewModel.currentItems(),
                currentFastReadItems: currentFastReadItems(),
                isCreating: customNarrationCreator.isCreating,
                onCreate: { selection in
                    await customNarrationCreator.create(from: selection)
                }
            )
            .presentationDetents([.medium, .large])
        }
        .task(id: customNarrationCreator.pollKey(isActive: shouldPollLongForm)) {
            await customNarrationCreator.pollIfNeeded(isActive: shouldPollLongForm)
        }
    }

    private var shouldPollLongForm: Bool {
        isActive && scenePhase == .active
    }

    private func startLongFormSummaryChat(items: [ContentSummary]) {
        guard !isStartingLongFormSummaryChat else { return }

        isStartingLongFormSummaryChat = true
        longFormSummaryError = nil
        let visibleContentIds = Array(
            items.lazy
                .filter { audioController.supportsAudioDiscussion(for: $0) }
                .prefix(15)
                .map(\.id)
        )

        Task { @MainActor in
            defer { isStartingLongFormSummaryChat = false }

            do {
                let response = try await chatService.createAssistantTurn(
                    message: longFormSummaryPrompt,
                    screenContext: AssistantScreenContext(
                        screenType: "long_form_feed",
                        screenTitle: "Long Read",
                        visibleContentIds: visibleContentIds,
                        query: "choose a recent long-form article or podcast to summarize or discuss",
                        note: (
                            "Ask the user which visible Long Read article or podcast they want to "
                            + "interact with before summarizing. Present the visible options by title "
                            + "when possible. Prefer in-app content before web search."
                        )
                    )
                )
                ChatNavigationCoordinator.shared.openAssistantTurn(response)
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                longFormSummaryError = error.localizedDescription
            }
        }
    }

    private var longFormSummaryPrompt: String {
        (
            "Ask me which recent long-form article or podcast I would like to "
            + "interact with. Use the visible Long Read articles and podcasts as "
            + "options, and wait for my choice before summarizing or discussing it."
        )
    }

    @ViewBuilder
    private func cardLink(content: ContentSummary) -> some View {
        LongFormCard(
            content: content,
            playbackService: audioController.playbackService,
            isAudioSupported: audioController.supportsAudioDiscussion(for: content),
            isAudioPreparing: audioController.isAudioPreparing(for: content),
            isAudioPlaying: audioController.isAudioPlaying(for: content),
            isAudioControlVisible: audioController.shouldShowAudioControls(for: content),
            audioTarget: audioController.audioTarget(for: content),
            audioErrorMessage: audioController.errorMessage(for: content),
            onMarkRead: {
                Task { await viewModel.markAsRead(content.id) }
            },
            onToggleKnowledgeSave: {
                Task {
                    await viewModel.toggleKnowledgeSave(content.id)
                }
            },
            onDigDeeper: { selectedText in
                let visibleContentIds = viewModel.currentItems().prefix(15).map(\.id)
                FeedDigDeeperAction.start(
                    selectedText: selectedText,
                    item: content,
                    visibleContentIds: visibleContentIds,
                    surface: .longForm
                )
            },
            onOpen: {
                openContent(content)
            },
            onToggleAudio: {
                audioController.handleAudioDiscussion(for: content)
            }
        )
        .matchedContentZoomSource(id: content.id, namespace: contentTransitionNamespace)
        .accessibilityIdentifier("long.row.\(content.id)")
        .onAppear {
            prefetchUpcomingCardImages(after: content)
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    private func prefetchUpcomingCardImages(after content: ContentSummary) {
        let items = viewModel.currentItems()
        guard let index = items.firstIndex(where: { $0.id == content.id }) else { return }
        let upcoming = Array(items.dropFirst(index + 1).prefix(3))
        guard !upcoming.isEmpty else { return }
        ContentImagePrefetcher.prefetch(contents: upcoming)
    }

    private func openContent(_ content: ContentSummary) {
        ContentImagePrefetcher.prefetch(content)
        onSelect(ContentDetailRoute(
            summary: content,
            allContentIds: viewModel.currentItems().map(\.id),
            navigationSurface: .longForm
        ))
    }

    @ViewBuilder
    private var longFormEmptyState: some View {
        // Sorted once here; the bootstrap helpers below all take this list as a
        // parameter so a single render pass doesn't re-filter and re-sort per access.
        let sources = longFormSources
        if shouldShowBootstrapState(sources: sources) {
            LongFormBootstrapStateView(
                sources: sources,
                isLoading: sourcesViewModel.isLoading,
                onRefresh: { await refreshLongFormSurface(forceReload: true) }
            )
        } else if unreadCountService.longFormCount == 0 && LongFormBootstrapStateView.totalProcessedSourceItems(in: sources) > 0 {
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

    private var longFormSources: [ScraperConfig] {
        sourcesViewModel.configs
            .filter { $0.isActive }
            .sorted(by: LongFormBootstrapStateView.compareSources)
    }

    private var shouldUseBootstrapSourceState: Bool {
        viewModel.currentItems().isEmpty && unreadCountService.longFormCount == 0
    }

    private func shouldShowBootstrapState(sources: [ScraperConfig]) -> Bool {
        guard shouldUseBootstrapSourceState else { return false }
        if sourcesViewModel.isLoading {
            return true
        }
        return hasLoadedBootstrapSources
            && LongFormBootstrapStateView.totalProcessedSourceItems(in: sources) == 0
            && !sources.isEmpty
    }

    @MainActor
    private func refreshLongFormSurface(forceReload: Bool) async {
        if forceReload {
            if viewModel.currentItems().isEmpty {
                await viewModel.refreshUnreadFeed()
            } else {
                await viewModel.refreshUnreadFeedInBackground()
            }
        } else {
            await viewModel.ensureUnreadFeedLoaded()
        }

        await unreadCountService.refreshCounts()
        await refreshSourcesIfNeeded()
    }

    @MainActor
    private func runLongFormPollingLoop() async {
        guard await TabActivationTiming.waitForSettle(), shouldPollLongForm else { return }
        await refreshLongFormSurface(forceReload: false)

        while !Task.isCancelled {
            let interval: Duration = viewModel.currentItems().isEmpty ? .seconds(5) : .seconds(30)
            do {
                try await Task.sleep(for: interval)
            } catch {
                break
            }

            guard shouldPollLongForm else { break }
            await refreshLongFormSurface(forceReload: true)
        }
    }

    @MainActor
    private func refreshSourcesIfNeeded() async {
        guard shouldUseBootstrapSourceState else { return }
        guard !hasLoadedBootstrapSources else { return }
        hasLoadedBootstrapSources = true
        await sourcesViewModel.loadConfigs()
    }
}
