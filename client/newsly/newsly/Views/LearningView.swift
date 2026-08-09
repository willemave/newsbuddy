//
//  LearningView.swift
//  newsly
//

import Foundation
import SwiftUI

struct LearningView: View {
    let scrollToTopRequest: Int
    let onSelectSession: (ChatSessionRoute) -> Void
    let chatTransitionNamespace: Namespace.ID?
    let contentTextSize: DynamicTypeSize
    var onOpenMore: (() -> Void)?

    @State private var viewModel: LearningHubViewModel
    @State private var narrations: CustomNarrationLibraryViewModel
    @State private var decks = RootDependencyFactory.makeLearningDecksViewModel()
    @State private var settings = AppSettings.shared
    @State private var composerText = ""
    @State private var deckReaderDestination: LearningDeckReaderDestination?
    @State private var timeline: [LearningTimelineItem] = []
    @FocusState private var isComposerFocused: Bool

    private static let topAnchor = "learning.top"

    init(
        scrollToTopRequest: Int = 0,
        onSelectSession: @escaping (ChatSessionRoute) -> Void,
        onOpenMore: (() -> Void)? = nil,
        viewModel: LearningHubViewModel,
        readStateCache: ReadStateCache,
        contentTextSize: DynamicTypeSize,
        chatTransitionNamespace: Namespace.ID? = nil
    ) {
        self.scrollToTopRequest = scrollToTopRequest
        self.onSelectSession = onSelectSession
        self.onOpenMore = onOpenMore
        self.viewModel = viewModel
        self.contentTextSize = contentTextSize
        self.chatTransitionNamespace = chatTransitionNamespace
        self._narrations = State(
            initialValue: RootDependencyFactory.makeCustomNarrationLibraryViewModel(
                readStateCache: readStateCache
            )
        )
    }

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    private var trimmedComposerText: String {
        composerText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var timelineRevision: LearningTimelineRevision {
        LearningTimelineRevision(
            chats: viewModel.timelineRevision,
            decks: decks.timelineRevision,
            narrations: narrations.timelineRevision
        )
    }

    var body: some View {
        ScrollViewReader { proxy in
            List {
                EditorialMastheadHeader(
                    title: "Learning",
                    titleAccessibilityIdentifier: "learning.screen",
                    trailingAccessory: onOpenMore.map { action in
                        AnyView(moreMenuButton(action))
                    }
                )
                .appListRow()
                .id(Self.topAnchor)

                composer
                    .padding(.bottom, 24)
                    .appListRow()

                learningErrors
                timelineContent
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .contentMargins(.bottom, 32, for: .scrollContent)
            .environment(\.defaultMinListRowHeight, 1)
            .onPaginationThresholdReached {
                await viewModel.loadMoreSessions()
            }
            .refreshable { await loadLearningScreen() }
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
            await loadLearningScreen()
            await viewModel.checkAndRefreshVoiceDictation()
        }
        .task(id: viewModel.hasActiveChatWork) {
            await viewModel.pollActiveChatWork()
        }
        .onChange(of: timelineRevision, initial: true) { _, _ in
            rebuildTimeline()
        }
        .onChange(of: viewModel.completedVoiceRoute) { _, route in
            guard let route else { return }
            viewModel.clearCompletedVoiceRoute()
            onSelectSession(route)
        }
        .onDisappear {
            viewModel.cancelVoiceRecording()
            narrations.cancelPolling()
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
                .accessibilityLabel("Ask Learning")
                .accessibilityIdentifier("learning.chat.input")

            if !trimmedComposerText.isEmpty && !viewModel.isVoiceRecording {
                Button(action: sendComposerMessage) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.appSymbol(size: 28))
                        .foregroundStyle(
                            viewModel.isCreatingSession ? Color.onSurfaceSecondary : Color.brandPrimary
                        )
                        .frame(width: 44, height: 44)
                }
                .disabled(viewModel.isCreatingSession)
                .accessibilityLabel("Send question")
                .accessibilityIdentifier("learning.chat.send")
            } else {
                TapToTalkMicButton(
                    isEnabled: viewModel.isVoiceRecording ||
                        (!viewModel.isCreatingSession && !viewModel.isVoiceActionInFlight && !viewModel.isVoiceTranscribing),
                    isRecording: viewModel.isVoiceRecording,
                    isTranscribing: viewModel.isVoiceTranscribing,
                    isBusy: !viewModel.isVoiceRecording &&
                        (viewModel.isCreatingSession || viewModel.isVoiceActionInFlight || viewModel.isVoiceTranscribing),
                    size: 36,
                    action: {
                        Task {
                            if let route = await viewModel.toggleVoiceRecording() {
                                onSelectSession(route)
                            }
                        }
                    }
                )
                .frame(width: 44, height: 44)
                .accessibilityIdentifier("learning.chat.mic")
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
    private var learningErrors: some View {
        if viewModel.loadErrorMessage != nil {
            LearningInlineError(
                message: "Chats couldn't be loaded.",
                actionTitle: "Try Again",
                accessibilityIdentifier: "learning.error.chats.load",
                action: { Task { await viewModel.loadLearning() } }
            )
            .appListRow()
        }
        if let message = viewModel.errorMessage {
            LearningInlineError(
                message: message,
                actionTitle: "Dismiss",
                accessibilityIdentifier: "learning.error.chats.action",
                action: viewModel.clearError
            )
            .appListRow()
        }
        if decks.loadErrorMessage != nil {
            LearningInlineError(
                message: "Learning Decks couldn't be loaded.",
                actionTitle: "Try Again",
                accessibilityIdentifier: "learning.error.decks.load",
                action: { Task { await decks.load() } }
            )
            .appListRow()
        }
        if let message = decks.errorMessage {
            LearningInlineError(
                message: message,
                actionTitle: "Dismiss",
                accessibilityIdentifier: "learning.error.decks.action",
                action: decks.clearError
            )
            .appListRow()
        }
        if narrations.loadErrorMessage != nil {
            LearningInlineError(
                message: "Narrations couldn't be loaded.",
                actionTitle: "Try Again",
                accessibilityIdentifier: "learning.error.narrations.load",
                action: { Task { await narrations.load() } }
            )
            .appListRow()
        }
        if let message = narrations.errorMessage {
            LearningInlineError(
                message: message,
                actionTitle: "Dismiss",
                accessibilityIdentifier: "learning.error.narrations.action",
                action: narrations.clearError
            )
            .appListRow()
        }
    }

    @ViewBuilder
    private var timelineContent: some View {
        let items = timeline
        if viewModel.isLoading && items.isEmpty {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Loading learning activity")
                    .font(.terracottaBodyMedium)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 20)
            .appListRow()
        } else if items.isEmpty {
            EmptyStateView(
                icon: "sparkles",
                title: "Start learning",
                subtitle: "Ask a question, create a Learning Deck, or build a narration to begin."
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 28)
            .appListRow()
        } else {
            // Flat list, no day dividers: with roughly one entry per day the rules
            // outnumbered the content. Each row carries its own date under the icon.
            ForEach(items) { item in
                timelineRow(item)
            }

            if viewModel.isLoadingMore {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 16)
                    .accessibilityIdentifier("learning.pagination.loading")
                    .appListRow()
            } else if viewModel.hasLoadMoreError {
                Button("Try loading more again") {
                    Task { await viewModel.loadMoreSessions() }
                }
                .frame(maxWidth: .infinity)
                .accessibilityIdentifier("learning.pagination.retry")
                .appListRow()
            }
        }
    }

    @ViewBuilder
    private func timelineRow(_ item: LearningTimelineItem) -> some View {
        switch item {
        case .chat(let session, let preview):
            Button {
                onSelectSession(ChatSessionRoute(session: session))
            } label: {
                LearningChatRow(
                    session: session,
                    activityDate: item.activityDate,
                    preview: preview
                )
            }
            .buttonStyle(.plain)
            .matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)
            .accessibilityIdentifier("learning.chat.\(session.id)")
            .accessibilityValue(
                session.isPreparingChat || session.isProcessing ? "Preparing" : ""
            )
            .appListRow()
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                Button(role: .destructive) {
                    Task { await viewModel.deleteSession(session) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
        case .deck(let deck):
            Button {
                Task { await openDeck(deck) }
            } label: {
                LearningDeckTimelineRow(deck: deck, activityDate: item.activityDate)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("learning.deck.\(deck.id)")
            .accessibilityValue(deck.hasActiveLatestRun ? deck.statusLabel : "")
            .appListRow()
            .contextMenu {
                Button {
                    Task { await regenerateDeck(deck) }
                } label: {
                    Label("Regenerate", systemImage: "arrow.clockwise")
                }
                .disabled(deck.hasActiveLatestRun || decks.busyDeckIDs.contains(deck.id))
                .accessibilityIdentifier("learning.deck.\(deck.id).regenerate")

                Button(role: .destructive) {
                    Task { await decks.delete(deck) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                Button(role: .destructive) {
                    Task { await decks.delete(deck) }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            }
        case .narration(let episode):
            Button {
                Task {
                    if episode.isFailed {
                        await narrations.retry(episode)
                    } else {
                        await narrations.handleTap(episode)
                    }
                }
            } label: {
                LearningNarrationRow(
                    episode: episode,
                    activityDate: item.activityDate,
                    subtitle: narrations.subtitle(for: episode),
                    isPlaying: narrations.isPlaying(episode)
                )
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("learning.narration.\(episode.id)")
            .appListRow()
        }
    }

    private func moreMenuButton(_ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: "line.3.horizontal")
                .font(.appSymbol(size: 20, weight: .semibold))
                .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .frame(width: 44, height: 44)
        .contentShape(Rectangle())
        .foregroundStyle(Color.onSurface)
        .accessibilityLabel("Settings and more")
        .accessibilityIdentifier("learning.more_menu")
    }

    private func sendComposerMessage() {
        let message = trimmedComposerText
        guard !message.isEmpty else { return }
        isComposerFocused = false
        Task {
            if let route = await viewModel.startChat(message: message) {
                composerText = ""
                onSelectSession(route)
            } else {
                isComposerFocused = true
            }
        }
    }

    private func rebuildTimeline() {
        timeline = LearningTimelineItem.merged(
            chats: viewModel.sessions,
            decks: decks.decks,
            narrations: narrations.episodes
        )
    }

    @MainActor
    private func openDeck(_ deck: LearningDeck) async {
        let url = deck.viewerAvailable ? await decks.viewerURL(for: deck) : nil
        deckReaderDestination = LearningDeckReaderDestination(deck: deck, url: url)
    }

    @MainActor
    private func regenerateDeck(_ deck: LearningDeck) async {
        guard await decks.regenerate(deck) != nil else { return }
        ToastService.shared.show("Regenerating your deck", type: .info)
    }

    @MainActor
    private func loadLearningScreen() async {
        async let chatLoad: Void = viewModel.loadLearning()
        async let narrationLoad: Void = narrations.load()
        async let deckLoad: Void = decks.load()
        _ = await (chatLoad, narrationLoad, deckLoad)
        // The three initial requests can finish before SwiftUI has installed the
        // revision observer. Project their completed state explicitly so a fast
        // response cannot leave the timeline displaying its initial empty value.
        rebuildTimeline()
    }
}

private struct LearningChatRow: View {
    let session: ChatSessionSummary
    let activityDate: Date
    let preview: String?

    private var isPreparing: Bool {
        session.isPreparingChat || session.isProcessing
    }

    var body: some View {
        LearningTimelineRow(
            icon: "bubble.left.and.bubble.right",
            isBusy: isPreparing,
            busyAccessibilityIdentifier: isPreparing
                ? "learning.chat.\(session.id).preparing"
                : nil,
            activityDate: activityDate,
            title: session.displayTitle,
            subtitle: preview
        ) {
            Image(systemName: "chevron.right")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
        }
    }
}

private struct LearningTimelineRevision: Equatable {
    let chats: Int
    let decks: Int
    let narrations: Int
}

private struct LearningDeckTimelineRow: View {
    let deck: LearningDeck
    let activityDate: Date

    var body: some View {
        LearningTimelineRow(
            icon: "rectangle.on.rectangle",
            isBusy: deck.hasActiveLatestRun,
            busyAccessibilityIdentifier: deck.hasActiveLatestRun
                ? "learning.deck.\(deck.id).preparing"
                : nil,
            activityDate: activityDate,
            title: deck.displayTitle,
            subtitle: deck.timelineSubtitle
        ) {
            Image(systemName: "chevron.right")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
        }
    }
}

private struct LearningNarrationRow: View {
    let episode: AudioEpisode
    let activityDate: Date
    let subtitle: String
    let isPlaying: Bool

    var body: some View {
        LearningTimelineRow(
            icon: "waveform",
            activityDate: activityDate,
            title: episode.title,
            subtitle: subtitle
        ) {
            Image(
                systemName: episode.isFailed
                    ? "arrow.clockwise"
                    : (isPlaying ? "pause.fill" : "play.fill")
            )
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.brandPrimary)
                .frame(width: 30, height: 30)
                .background(Color.surfaceSecondary)
                .clipShape(Circle())
        }
    }
}

private struct LearningInlineError: View {
    let message: String
    let actionTitle: String
    let accessibilityIdentifier: String
    let action: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.circle")
                .foregroundStyle(Color.onSurfaceSecondary)
                .accessibilityHidden(true)
            Text(message)
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
            Spacer(minLength: 8)
            Button(actionTitle, action: action)
                .buttonStyle(.bordered)
                .accessibilityIdentifier("\(accessibilityIdentifier).action")
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.vertical, 10)
        .accessibilityIdentifier(accessibilityIdentifier)
    }
}

/// Every timeline entry is the same two lines: a title that truncates rather than
/// wraps, and the latest line of content under it. State that used to occupy its
/// own text line (PREPARING CHAT, READY, NARRATION) is carried by the artwork glyph
/// and a busy dot instead, so rows stay a fixed height whatever they are doing.
///
/// The date rides under the icon rather than in a day-divider bar above a group of
/// rows — the timeline averages about one entry per day, so the dividers outnumbered
/// the entries they were grouping.
private struct LearningTimelineRow<Accessory: View>: View {
    let icon: String
    var isBusy = false
    var busyAccessibilityIdentifier: String?
    let activityDate: Date
    let title: String
    let subtitle: String?
    @ViewBuilder var accessory: () -> Accessory

    var body: some View {
        HStack(spacing: 12) {
            VStack(spacing: 3) {
                LearningArtwork(
                    icon: icon,
                    isBusy: isBusy,
                    busyAccessibilityIdentifier: busyAccessibilityIdentifier
                )

                Text(ContentTimestampFormatter.compactRelativeText(from: activityDate))
                    .font(.terracottaLabelSmall)
                    .foregroundStyle(Color.onSurfaceTertiary)
                    .lineLimit(1)
            }
            // Optical, not geometric: the serif title's tall ascenders push the text
            // block's visual mass below its layout center, so a tile centered by the
            // stack reads high. Nudge down to match.
            .offset(y: 2)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(1)
                    .truncationMode(.tail)

                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            accessory()
        }
        .learningTimelineRow()
    }
}

private struct LearningArtwork: View {
    let icon: String
    var isBusy = false
    var busyAccessibilityIdentifier: String?

    private let size: CGFloat = 38

    var body: some View {
        ZStack {
            Color.surfaceSecondary
            // Neutral: every row in the timeline carries one of these, so accenting
            // them accented the whole screen. Regular weight rather than semibold —
            // multi-stroke glyphs like the deck's stacked rectangles turn to mush
            // when they are both small and heavy.
            Image(systemName: icon)
                .font(.appSymbol(size: 16))
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.45), lineWidth: 0.5)
        }
        .overlay(alignment: .topTrailing) {
            if isBusy {
                PreparingActivityDot()
                    .padding(2.5)
                    .background(Color.surfacePrimary, in: Circle())
                    .offset(x: 3, y: -3)
                    .accessibilityIdentifier(ifPresent: busyAccessibilityIdentifier)
            }
        }
    }
}

/// Breathing dot standing in for a spinner on rows still being prepared.
/// A `.mini` `ProgressView` renders as a low-resolution aperture at this size
/// and read as a rendering artifact sitting next to the timestamp.
private struct PreparingActivityDot: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isDimmed = false

    var body: some View {
        Circle()
            .fill(Color.brandPrimary)
            .frame(width: 5, height: 5)
            .opacity(isDimmed ? 0.3 : 1)
            .animation(reduceMotion ? nil : AppMotion.chatStatusPulse, value: isDimmed)
            .onAppear { isDimmed = !reduceMotion }
            .onChange(of: reduceMotion) { _, newValue in isDimmed = !newValue }
            .accessibilityHidden(true)
    }
}

private extension View {
    func learningTimelineRow() -> some View {
        self
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
    }
}
