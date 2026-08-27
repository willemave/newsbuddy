//
//  KnowledgeTimelineView.swift
//  newsly
//

import Foundation
import SwiftUI

struct KnowledgeView: View {
    let scrollToTopRequest: Int
    let onSelectContent: (ContentDetailRoute) -> Void
    let onSelectSession: (ChatSessionRoute) -> Void
    let onSearch: () -> Void
    let chatTransitionNamespace: Namespace.ID?
    let contentTextSize: DynamicTypeSize
    var onOpenMore: (() -> Void)?

    @State private var viewModel: KnowledgeTimelineViewModel
    @State private var settings = AppSettings.shared
    @State private var composerText = ""
    @State private var deckReaderDestination: LearningDeckReaderDestination?
    @State private var showsInitialLoadingIndicator = false
    @FocusState private var isComposerFocused: Bool

    private static let topAnchor = "knowledge.top"

    init(
        scrollToTopRequest: Int = 0,
        onSelectContent: @escaping (ContentDetailRoute) -> Void,
        onSelectSession: @escaping (ChatSessionRoute) -> Void,
        onSearch: @escaping () -> Void,
        onOpenMore: (() -> Void)? = nil,
        viewModel: KnowledgeTimelineViewModel,
        contentTextSize: DynamicTypeSize,
        chatTransitionNamespace: Namespace.ID? = nil
    ) {
        self.scrollToTopRequest = scrollToTopRequest
        self.onSelectContent = onSelectContent
        self.onSelectSession = onSelectSession
        self.onSearch = onSearch
        self.onOpenMore = onOpenMore
        self.viewModel = viewModel
        self.contentTextSize = contentTextSize
        self.chatTransitionNamespace = chatTransitionNamespace
    }

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    private var trimmedComposerText: String {
        composerText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var isWaitingForInitialTimeline: Bool {
        viewModel.isLoading && viewModel.timeline.isEmpty
    }

    var body: some View {
        ScrollViewReader { proxy in
            List {
                EditorialMastheadHeader(
                    title: "Knowledge",
                    titleAccessibilityIdentifier: "knowledge.screen",
                    trailingAccessory: AnyView(headerActions)
                )
                .appListRow()
                .id(Self.topAnchor)

                composer
                    .padding(.bottom, 24)
                    .appListRow()

                knowledgeErrors
                timelineContent
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .contentMargins(.bottom, 32, for: .scrollContent)
            .environment(\.defaultMinListRowHeight, 1)
            .onPaginationThresholdReached {
                await viewModel.loadNextPage()
            }
            .refreshable { await viewModel.load() }
            .topScreenEdgeFade()
            .bottomScreenEdgeFade()
            .scrollsToTopOnRequest(
                scrollToTopRequest,
                anchor: Self.topAnchor,
                using: proxy
            )
        }
        .dynamicTypeSize(appTextSize)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .fullScreenCover(item: $deckReaderDestination) { destination in
            LearningDeckReaderView(
                deck: destination.deck,
                viewerURL: destination.url,
                onClose: { deckReaderDestination = nil }
            )
            .dynamicTypeSize(contentTextSize)
            .ignoresSafeArea()
        }
        .task {
            async let screenLoad: Void = viewModel.load()
            async let voiceRefresh: Void = viewModel.chats.checkAndRefreshVoiceDictation()
            _ = await (screenLoad, voiceRefresh)
        }
        .task(id: isWaitingForInitialTimeline) {
            await updateInitialLoadingIndicator()
        }
        .task(id: viewModel.chats.hasActiveChatWork) {
            await viewModel.chats.pollActiveChatWork()
        }
        .onChange(of: viewModel.chats.completedVoiceRoute) { _, route in
            guard let route else { return }
            viewModel.chats.clearCompletedVoiceRoute()
            onSelectSession(route)
        }
        .onDisappear {
            viewModel.cancelTransientWork()
        }
    }

    private var composer: some View {
        HStack(spacing: 10) {
            Image(systemName: "text.bubble")
                .font(.appSymbol(size: 16, weight: .medium))
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityHidden(true)

            TextField("Ask anything…", text: $composerText, axis: .vertical)
                .font(.terracottaBodyLarge)
                .focused($isComposerFocused)
                .lineLimit(1...4)
                .submitLabel(.send)
                .onSubmit(sendComposerMessage)
                .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                .accessibilityLabel("Ask Knowledge")
                .accessibilityIdentifier("knowledge.chat.input")

            if !trimmedComposerText.isEmpty && !viewModel.chats.isVoiceRecording {
                Button(action: sendComposerMessage) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.appSymbol(size: 28))
                        .foregroundStyle(
                            viewModel.chats.isCreatingSession ? Color.onSurfaceSecondary : Color.brandPrimary
                        )
                        .frame(width: 44, height: 44)
                }
                .disabled(viewModel.chats.isCreatingSession)
                .accessibilityLabel("Send question")
                .accessibilityIdentifier("knowledge.chat.send")
            } else {
                TapToTalkMicButton(
                    isEnabled: viewModel.chats.isVoiceRecording ||
                        (!viewModel.chats.isCreatingSession && !viewModel.chats.isVoiceActionInFlight && !viewModel.chats.isVoiceTranscribing),
                    isRecording: viewModel.chats.isVoiceRecording,
                    isTranscribing: viewModel.chats.isVoiceTranscribing,
                    isBusy: !viewModel.chats.isVoiceRecording &&
                        (viewModel.chats.isCreatingSession || viewModel.chats.isVoiceActionInFlight || viewModel.chats.isVoiceTranscribing),
                    size: 36,
                    action: {
                        Task {
                            if let route = await viewModel.chats.toggleVoiceRecording() {
                                onSelectSession(route)
                            }
                        }
                    }
                )
                .frame(width: 44, height: 44)
                .accessibilityIdentifier("knowledge.chat.mic")
            }
        }
        .padding(.leading, 16)
        .padding(.trailing, 8)
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                .stroke(Color.borderSubtle, lineWidth: 1)
        )
        .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    @ViewBuilder
    private var knowledgeErrors: some View {
        ForEach(viewModel.failures) { failure in
            KnowledgeTimelineInlineError(
                message: failure.message,
                actionTitle: failure.actionTitle,
                accessibilityIdentifier: failure.accessibilityIdentifier,
                action: { Task { await viewModel.recover(failure) } }
            )
            .appListRow()
        }
    }

    @ViewBuilder
    private var timelineContent: some View {
        let items = viewModel.timeline
        if viewModel.isLoading && items.isEmpty {
            if showsInitialLoadingIndicator {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("Loading knowledge")
                        .font(.terracottaBodyMedium)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.vertical, 20)
                .appListRow()
            }
        } else if items.isEmpty {
            EmptyStateView(
                icon: "books.vertical",
                title: "Your knowledge starts here",
                subtitle: "Save or share something, or ask anything to begin."
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 28)
            .appListRow()
        } else {
            ForEach(viewModel.groupedTimeline) { group in
                KnowledgeDayDelimiter(title: group.title)
                    .appListRow()
                ForEach(Array(group.items.enumerated()), id: \.element.id) { index, item in
                    timelineRow(item)
                    if index < group.items.count - 1 {
                        KnowledgeTimelineRowDivider()
                            .appListRow()
                    }
                }
            }

            if viewModel.isLoadingMore {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .accessibilityIdentifier("knowledge.pagination.loading")
                    .appListRow()
            } else if viewModel.hasPaginationError {
                Button("Try loading more again") {
                    Task { await viewModel.loadNextPage() }
                }
                .frame(maxWidth: .infinity)
                .accessibilityIdentifier("knowledge.pagination.retry")
                .appListRow()
            }
        }
    }

    private func updateInitialLoadingIndicator() async {
        guard isWaitingForInitialTimeline else {
            showsInitialLoadingIndicator = false
            return
        }
        showsInitialLoadingIndicator = false
        do {
            try await Task.sleep(for: .milliseconds(250))
        } catch {
            return
        }
        guard isWaitingForInitialTimeline, !Task.isCancelled else { return }
        showsInitialLoadingIndicator = true
    }

    @ViewBuilder
    private func timelineRow(_ item: KnowledgeTimelineItem) -> some View {
        switch item {
        case .saved(let content):
            KnowledgeSavedContentButton(
                content: content,
                accessibilityIdentifier: "knowledge.saved.\(content.id)",
                onOpen: {
                    onSelectContent(
                        ContentDetailRoute(
                            summary: content,
                            allContentIds: viewModel.savedContent.readyContentIDs,
                            navigationSurface: .savedLibrary
                        )
                    )
                },
                onRefresh: { Task { await viewModel.savedContent.loadKnowledgeLibrary() } },
                onRemove: { Task { await viewModel.savedContent.toggleKnowledgeSave(content.id) } }
            )
            .appListRow()
        case .chat(let session, let preview):
            Button {
                onSelectSession(ChatSessionRoute(session: session))
            } label: {
                KnowledgeChatRow(
                    session: session,
                    activityDate: item.activityDate,
                    preview: preview
                )
            }
            .buttonStyle(.plain)
            .matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)
            .accessibilityIdentifier("knowledge.chat.\(session.id)")
            .accessibilityValue(
                session.isPreparingChat || session.isProcessing ? "Preparing" : ""
            )
            .appListRow()
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                Button(role: .destructive) {
                    Task { await viewModel.chats.deleteSession(session) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
        case .deck(let deck):
            Button {
                Task { await openDeck(deck) }
            } label: {
                KnowledgeDeckTimelineRow(deck: deck, activityDate: item.activityDate)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("knowledge.deck.\(deck.id)")
            .accessibilityValue(deck.hasActiveLatestRun ? deck.statusLabel : "")
            .appListRow()
            .contextMenu {
                Button {
                    Task { await regenerateDeck(deck) }
                } label: {
                    Label("Regenerate", systemImage: "arrow.clockwise")
                }
                .disabled(
                    deck.hasActiveLatestRun || viewModel.decks.busyDeckIDs.contains(deck.id)
                )
                .accessibilityIdentifier("knowledge.deck.\(deck.id).regenerate")

                Button(role: .destructive) {
                    Task { await viewModel.decks.delete(deck) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                Button(role: .destructive) {
                    Task { await viewModel.decks.delete(deck) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
        case .narration(let episode):
            Button {
                Task {
                    if episode.isFailed {
                        await viewModel.narrations.retry(episode)
                    } else {
                        await viewModel.narrations.handleTap(episode)
                    }
                }
            } label: {
                KnowledgeNarrationRow(
                    episode: episode,
                    activityDate: item.activityDate,
                    subtitle: viewModel.narrations.subtitle(for: episode),
                    isPlaying: viewModel.narrations.isPlaying(episode)
                )
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("knowledge.narration.\(episode.id)")
            .appListRow()
        }
    }

    private var headerActions: some View {
        HStack(spacing: 2) {
            Button(action: onSearch) {
                Image(systemName: "magnifyingglass")
                    .font(.appSymbol(size: 19, weight: .semibold))
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Search saved knowledge")
            .accessibilityIdentifier("knowledge.search")

            if let onOpenMore {
                Button(action: onOpenMore) {
                    Image(systemName: "line.3.horizontal")
                        .font(.appSymbol(size: 19, weight: .semibold))
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Settings and more")
                .accessibilityIdentifier("knowledge.more_menu")
            }
        }
        .foregroundStyle(Color.onSurface)
    }

    private func sendComposerMessage() {
        let message = trimmedComposerText
        guard !message.isEmpty else { return }
        isComposerFocused = false
        Task {
            if let route = await viewModel.chats.startChat(message: message) {
                composerText = ""
                onSelectSession(route)
            } else {
                isComposerFocused = true
            }
        }
    }

    @MainActor
    private func openDeck(_ deck: LearningDeck) async {
        let url = deck.viewerAvailable ? await viewModel.decks.viewerURL(for: deck) : nil
        deckReaderDestination = LearningDeckReaderDestination(deck: deck, url: url)
    }

    @MainActor
    private func regenerateDeck(_ deck: LearningDeck) async {
        guard await viewModel.decks.regenerate(deck) != nil else { return }
        ToastService.shared.show("Regenerating your deck", type: .info)
    }
}
