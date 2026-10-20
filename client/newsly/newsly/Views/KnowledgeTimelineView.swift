//
//  KnowledgeTimelineView.swift
//  newsly
//

import Foundation
import SwiftUI

struct KnowledgeView: View {
    @Environment(AppLifecycle.self) private var lifecycle
    @Environment(XConnectionStore.self) private var xConnectionStore
    @Environment(RootDependencyFactory.self) private var dependencyFactory
    @Environment(RootTabBarMinimizer.self) private var tabBarMinimizer: RootTabBarMinimizer?
    @AppStorage("knowledge.sourceSwipeHintSeen") private var hasSeenSourceSwipeHint = false

    let scrollToTopRequest: Int
    let isVisible: Bool
    let onSelectContent: (ContentDetailRoute) -> Void
    let onSelectSession: (ChatSessionRoute) -> Void
    let onSearch: () -> Void
    let chatTransitionNamespace: Namespace.ID?
    let contentTextSize: DynamicTypeSize
    let onOpenSettings: () -> Void

    @State private var viewModel: KnowledgeTimelineViewModel
    @State private var composerText = ""
    @State private var deckReaderDestination: LearningDeckReaderDestination?
    @State private var deckSource: ContentSummary?
    @State private var showsInitialLoadingIndicator = false
    @FocusState private var isComposerFocused: Bool

    private static let topAnchor = "knowledge.top"

    init(
        scrollToTopRequest: Int,
        isVisible: Bool,
        onSelectContent: @escaping (ContentDetailRoute) -> Void,
        onSelectSession: @escaping (ChatSessionRoute) -> Void,
        onSearch: @escaping () -> Void,
        onOpenSettings: @escaping () -> Void,
        viewModel: KnowledgeTimelineViewModel,
        contentTextSize: DynamicTypeSize,
        chatTransitionNamespace: Namespace.ID? = nil
    ) {
        self.scrollToTopRequest = scrollToTopRequest
        self.isVisible = isVisible
        self.onSelectContent = onSelectContent
        self.onSelectSession = onSelectSession
        self.onSearch = onSearch
        self.onOpenSettings = onOpenSettings
        self.viewModel = viewModel
        self.contentTextSize = contentTextSize
        self.chatTransitionNamespace = chatTransitionNamespace
    }

    private var trimmedComposerText: String {
        composerText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// E2E launches hold the first prompt so visual baselines never capture a mid-roll frame.
    private var rotatesComposerPlaceholder: Bool {
        isVisible && !isComposerFocused && !E2ETestLaunch.isEnabled
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
            .contentMargins(.top, 0, for: .scrollContent)
            .contentMargins(.bottom, 32, for: .scrollContent)
            .environment(\.defaultMinListRowHeight, 1)
            .onPaginationThresholdReached {
                await viewModel.loadNextPage()
            }
            .refreshable { await viewModel.forceReload() }
            .systemTopScrollEdge()
            .onScrollGeometryChange(for: CGFloat.self) { geometry in
                RootTabBarMinimizer.scrolledDistance(in: geometry)
            } action: { _, offset in
                guard isVisible else { return }
                tabBarMinimizer?.record(offset: offset, source: "knowledge")
            }
            .scrollsToTopOnRequest(
                scrollToTopRequest,
                anchor: Self.topAnchor,
                using: proxy
            )
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $deckSource) { content in
            LearningDeckCreateSheet(
                sourceTitle: content.displayTitle,
                isSubmitting: viewModel.decks.isCreating,
                focusRecorder: dependencyFactory.makeLearningDeckFocusRecorder(),
                onCreate: { interestsPrompt in
                    await createDeck(from: content, interestsPrompt: interestsPrompt)
                }
            )
        }
        .fullScreenCover(item: $deckReaderDestination) { destination in
            LearningDeckReaderView(
                deck: destination.deck,
                viewerURL: destination.url,
                onClose: { deckReaderDestination = nil }
            )
            .dynamicTypeSize(contentTextSize)
            .ignoresSafeArea(.container)
        }
        .task(id: activationTaskID) {
            guard isVisible,
                  lifecycle.phase != .background,
                  let activation = lifecycle.activation else {
                return
            }
            async let screenLoad: Void = viewModel.activate(activation)
            async let voiceRefresh: Void = viewModel.chats.checkAndRefreshVoiceDictation()
            _ = await (screenLoad, voiceRefresh)
        }
        .task(id: savedObservationTaskID) {
            guard savedObservationTaskID.shouldObserve else { return }
            await viewModel.observeSavedKnowledge()
        }
        .task(id: isWaitingForInitialTimeline) {
            await updateInitialLoadingIndicator()
        }
        .task(id: activeWorkObservationID) {
            guard activeWorkObservationID.shouldObserve else { return }
            await viewModel.chats.pollActiveChatWork()
        }
        .onChange(of: lifecycle.phase) { _, phase in
            if phase == .background {
                viewModel.suspendAutomaticReads()
            }
        }
        .onChange(of: isVisible) { _, isVisible in
            if !isVisible {
                viewModel.suspendAutomaticReads()
            }
        }
        .onChange(of: viewModel.chats.completedVoiceRoute) { _, route in
            guard let route else { return }
            viewModel.chats.clearCompletedVoiceRoute()
            onSelectSession(route)
        }
        .onDisappear {
            viewModel.suspendAutomaticReads()
            viewModel.cancelTransientWork()
        }
    }

    private struct ActivationTaskID: Hashable {
        let generation: UInt64?
        let isVisible: Bool
    }

    private var activationTaskID: ActivationTaskID {
        ActivationTaskID(
            generation: lifecycle.activation?.generation,
            isVisible: isVisible
        )
    }

    private struct SavedObservationTaskID: Hashable {
        let isVisible: Bool
        let isActive: Bool
        let automaticReadsEnabled: Bool

        var shouldObserve: Bool { isVisible && isActive && automaticReadsEnabled }
    }

    private var savedObservationTaskID: SavedObservationTaskID {
        SavedObservationTaskID(
            isVisible: isVisible,
            isActive: lifecycle.phase == .active,
            automaticReadsEnabled: viewModel.automaticReadsEnabled
        )
    }

    private struct ActiveWorkObservationID: Hashable {
        let automaticReadsEnabled: Bool
        let hasActiveWork: Bool

        var shouldObserve: Bool {
            automaticReadsEnabled && hasActiveWork
        }
    }

    private var activeWorkObservationID: ActiveWorkObservationID {
        ActiveWorkObservationID(
            automaticReadsEnabled: viewModel.automaticReadsEnabled,
            hasActiveWork: viewModel.chats.hasActiveChatWork
        )
    }

    private var composer: some View {
        HStack(spacing: 10) {
            Image(systemName: "text.bubble")
                .font(.appSymbol(size: 16, weight: .medium))
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityHidden(true)

            TextField("", text: $composerText, axis: .vertical)
                .focused($isComposerFocused)
                .lineLimit(1...4)
                .submitLabel(.send)
                .onSubmit(sendComposerMessage)
                .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                .accessibilityLabel("Ask Knowledge")
                .accessibilityIdentifier("knowledge.chat.input")
                .overlay(alignment: .leading) {
                    if composerText.isEmpty {
                        KnowledgeComposerPlaceholder(isRotating: rotatesComposerPlaceholder)
                    }
                }
                .font(.terracottaBodyLarge)

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
                            await viewModel.chats.toggleVoiceRecording()
                        }
                    }
                )
                .frame(width: 44, height: 44)
                .accessibilityIdentifier("knowledge.chat.mic")
            }
        }
        .padding(.leading, 16)
        .padding(.trailing, 8)
        .glassEffect(
            .regular.interactive(),
            in: RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
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
            let swipeHintContentID = sourceSwipeHintContentID
            ForEach(viewModel.groupedTimeline) { group in
                KnowledgeDayDelimiter(title: group.title)
                    .appListRow()
                ForEach(Array(group.items.enumerated()), id: \.element.id) { index, item in
                    timelineRow(item, swipeHintContentID: swipeHintContentID)
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
    private func timelineRow(
        _ item: KnowledgeTimelineItem,
        swipeHintContentID: Int?
    ) -> some View {
        switch item {
        case .saved(let activity):
            let content = activity.content
            KnowledgeSavedContentButton(
                entry: activity,
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
                onReprocess: { await viewModel.savedContent.reprocessKnowledgeItem(content.id) },
                onRemove: { removeSaved(content) },
                derivatives: viewModel.sourceDerivatives[KnowledgeSource(content)] ?? [],
                onShortcut: { perform($0, for: content) }
            )
            .contextMenu {
                KnowledgeSourceActivityMenu(
                    entry: activity,
                    busyDeckIDs: viewModel.decks.busyDeckIDs,
                    perform: perform
                )
                Button(role: .destructive) {
                    removeSaved(content)
                } label: {
                    Label("Remove from Knowledge", systemImage: "bookmark.slash")
                }
            }
            .knowledgeSwipeHint(
                isActive: content.id == swipeHintContentID,
                onFinished: { hasSeenSourceSwipeHint = true }
            )
            .appListRow()
            .knowledgeSourceSwipeActions(
                for: content,
                onRemove: { removeSaved(content) },
                perform: { perform($0, for: content) }
            )
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
        GlassEffectContainer(spacing: 6) {
            HStack(spacing: 10) {
                Button(action: onSearch) {
                    Image(systemName: "magnifyingglass")
                        .font(.appSymbol(size: 17, weight: .semibold))
                        .frame(width: 44, height: 44)
                        .knowledgeHeaderGlass()
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Search saved knowledge")
                .accessibilityIdentifier("knowledge.search")

                Button(action: onOpenSettings) {
                    Image(systemName: "gearshape")
                        .font(.appSymbol(size: 17, weight: .semibold))
                        .overlay(alignment: .topTrailing) {
                            if xConnectionStore.needsAttention {
                                Circle()
                                    .fill(Color.statusDestructive)
                                    .frame(width: 9, height: 9)
                                    .overlay(Circle().stroke(Color.surfacePrimary, lineWidth: 2))
                                    .offset(x: 3, y: -2)
                                    .accessibilityIdentifier("knowledge.settings.attention")
                            }
                        }
                        .frame(width: 44, height: 44)
                        .knowledgeHeaderGlass()
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Settings")
                .accessibilityValue(xConnectionStore.needsAttention ? "Needs attention" : "")
                .accessibilityIdentifier("knowledge.settings")
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

    /// The newest ready saved row plays the one-time swipe hint, on first view or first save.
    /// E2E launches skip it so visual baselines never capture a half-open row.
    private var sourceSwipeHintContentID: Int? {
        guard !hasSeenSourceSwipeHint,
              !E2ETestLaunch.isEnabled,
              isVisible,
              !viewModel.isLoading else { return nil }
        for item in viewModel.timeline {
            if case .saved(let activity) = item, activity.content.savedLibraryItemState == .ready {
                return activity.content.id
            }
        }
        return nil
    }

    private func removeSaved(_ content: ContentSummary) {
        Task { await viewModel.savedContent.toggleKnowledgeSave(content.id) }
    }

    private func perform(_ action: KnowledgeSourceAction, for content: ContentSummary) {
        hasSeenSourceSwipeHint = true
        let source = KnowledgeSource(content)
        switch action {
        case .chat:
            Task {
                if let route = await viewModel.chats.openSourceChat(for: source) {
                    onSelectSession(route)
                }
            }
        case .deck:
            if let deck = viewModel.decks.decks.first(where: { $0.knowledgeSource == source }) {
                Task { await openDeck(deck) }
            } else {
                deckSource = content
            }
        case .listen:
            Task {
                guard await viewModel.narrations.narrate(source) else { return }
                dependencyFactory.toastService.show("Narration started", type: .info, duration: 2)
            }
        }
    }

    private func perform(_ action: KnowledgeRelatedAction) {
        switch action {
        case .openChat(let session):
            onSelectSession(ChatSessionRoute(session: session))
        case .deleteChat(let session):
            Task { await viewModel.chats.deleteSession(session) }
        case .openDeck(let deck):
            Task { await openDeck(deck) }
        case .regenerateDeck(let deck):
            Task { await regenerateDeck(deck) }
        case .deleteDeck(let deck):
            Task { await viewModel.decks.delete(deck) }
        }
    }

    @MainActor
    private func createDeck(from content: ContentSummary, interestsPrompt: String?) async -> Bool {
        let source = KnowledgeSource(content)
        let deck = await viewModel.decks.createDeck(
            contentId: source.contentId,
            newsItemId: source.newsItemId,
            interestsPrompt: interestsPrompt
        )
        guard deck != nil else { return false }
        dependencyFactory.toastService.show("Deck started", type: .info, duration: 2)
        return true
    }

    @MainActor
    private func openDeck(_ deck: LearningDeck) async {
        let url = deck.viewerAvailable ? await viewModel.decks.viewerURL(for: deck) : nil
        deckReaderDestination = LearningDeckReaderDestination(deck: deck, url: url)
    }

    @MainActor
    private func regenerateDeck(_ deck: LearningDeck) async {
        guard !deck.hasActiveLatestRun, !viewModel.decks.busyDeckIDs.contains(deck.id) else { return }
        guard await viewModel.decks.regenerate(deck) != nil else { return }
        dependencyFactory.toastService.show("Regenerating your deck", type: .info, duration: 3)
    }
}

private extension View {
    /// Masthead actions sit on glass discs, matching Briefing's listen button.
    /// The explicit shape matters: a plain-style button only hit-tests drawn
    /// pixels, and the gear's hollow centre used to swallow taps.
    func knowledgeHeaderGlass() -> some View {
        glassEffect(.regular.interactive(), in: .circle)
            .contentShape(Circle())
    }
}
