//
//  LearningView.swift
//  newsly
//

import SwiftUI

enum LearningFocusTarget: Hashable {
    case narrations
}

struct LearningFocusRequest: Equatable {
    let id = UUID()
    let target: LearningFocusTarget
}

private enum LearningSheetDestination: String, Identifiable {
    case narrationList

    var id: String { rawValue }
}

struct LearningView: View {
    let focusRequest: LearningFocusRequest?
    let onFocusHandled: (LearningFocusRequest) -> Void
    let onSelectSession: (ChatSessionRoute) -> Void
    let chatTransitionNamespace: Namespace.ID?
    let contentTextSize: DynamicTypeSize
    var onOpenMore: (() -> Void)?

    @State private var viewModel: LearningHubViewModel
    @State private var narrations: CustomNarrationLibraryViewModel
    @State private var decks = RootDependencyFactory.makeLearningDecksViewModel()
    @State private var settings = AppSettings.shared
    @State private var composerText = ""
    @State private var activeSheet: LearningSheetDestination?
    @State private var deckReaderDestination: LearningDeckReaderDestination?
    @FocusState private var isComposerFocused: Bool
    init(
        focusRequest: LearningFocusRequest? = nil,
        onFocusHandled: @escaping (LearningFocusRequest) -> Void = { _ in },
        onSelectSession: @escaping (ChatSessionRoute) -> Void,
        onOpenMore: (() -> Void)? = nil,
        viewModel: LearningHubViewModel,
        readStateCache: ReadStateCache,
        contentTextSize: DynamicTypeSize,
        chatTransitionNamespace: Namespace.ID? = nil
    ) {
        self.focusRequest = focusRequest
        self.onFocusHandled = onFocusHandled
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

    private var timeline: [LearningTimelineItem] {
        LearningTimelineItem.merged(
            chats: viewModel.sessions,
            decks: decks.decks,
            narrations: narrations.episodes
        )
    }

    var body: some View {
        List {
            EditorialMastheadHeader(
                title: "Learning",
                trailingAccessory: onOpenMore.map { action in
                    AnyView(moreMenuButton(action))
                }
            )
            .appListRow()

            composer
                .padding(.bottom, 24)
                .appListRow()

            timelineContent
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .contentMargins(.bottom, 32, for: .scrollContent)
        .environment(\.defaultMinListRowHeight, 1)
        .accessibilityIdentifier("learning.screen")
        .refreshable { await loadLearningScreen() }
        .topScreenEdgeFade()
        .bottomScreenEdgeFade()
        .dynamicTypeSize(appTextSize)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $activeSheet) { destination in
            switch destination {
            case .narrationList:
                CustomNarrationListSheet(
                    viewModel: narrations,
                    playbackService: narrations.playbackService
                )
            }
        }
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
            handleFocusRequest(focusRequest)
        }
        .task(id: viewModel.hasActiveChatWork) {
            await viewModel.pollActiveChatWork()
        }
        .onChange(of: focusRequest) { _, request in handleFocusRequest(request) }
        .onChange(of: viewModel.completedVoiceRoute) { _, route in
            guard let route else { return }
            viewModel.clearCompletedVoiceRoute()
            onSelectSession(route)
        }
        .onDisappear { viewModel.cancelVoiceRecording() }
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
    private var timelineContent: some View {
        if viewModel.isLoading && timeline.isEmpty {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Loading learning activity")
                    .font(.terracottaBodyMedium)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 20)
            .appListRow()
        } else if timeline.isEmpty {
            EmptyStateView(
                icon: "sparkles",
                title: "Start learning",
                subtitle: "Chats, Learning Decks, and narrations will appear together here."
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 28)
            .appListRow()
        } else {
            // Flat list, no day dividers: with roughly one entry per day the rules
            // outnumbered the content. Each row carries its own date under the icon.
            ForEach(timeline) { item in
                timelineRow(item)
            }
        }
    }

    @ViewBuilder
    private func timelineRow(_ item: LearningTimelineItem) -> some View {
        switch item {
        case .chat(let session):
            Button {
                onSelectSession(ChatSessionRoute(session: session))
            } label: {
                LearningChatRow(session: session, activityDate: item.activityDate)
            }
            .buttonStyle(.plain)
            .matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)
            .accessibilityIdentifier("learning.chat.\(session.id)")
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
                Task { await narrations.handleTap(episode) }
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
        composerText = ""
        Task {
            if let route = await viewModel.startChat(message: message) {
                onSelectSession(route)
            }
        }
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

    private func handleFocusRequest(_ request: LearningFocusRequest?) {
        guard let request else { return }
        if request.target == .narrations { activeSheet = .narrationList }
        onFocusHandled(request)
    }

    @MainActor
    private func loadLearningScreen() async {
        async let chatLoad: Void = viewModel.loadLearning()
        async let narrationLoad: Void = narrations.load()
        async let deckLoad: Void = decks.load()
        _ = await (chatLoad, narrationLoad, deckLoad)
    }
}

private struct LearningChatRow: View {
    let session: ChatSessionSummary
    let activityDate: Date

    private var preview: String? {
        if let lastMessagePreview = session.lastMessagePreview?.trimmingCharacters(in: .whitespacesAndNewlines),
           !lastMessagePreview.isEmpty {
            return previewText(lastMessagePreview)
        }
        if let articleSummary = session.articleSummary?.trimmingCharacters(in: .whitespacesAndNewlines),
           !articleSummary.isEmpty {
            return "About: \(previewText(articleSummary))"
        }
        return session.displaySubtitle.map(previewText)
    }

    private func previewText(_ markdown: String) -> String {
        ShareContent.plainText(fromMarkdown: markdown)
            .replacingOccurrences(of: #"(?m)^\s{0,3}(#{1,6}(\s+|$)|>\s+|[-*+]\s+|\d+\.\s+)"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

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
            Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.brandPrimary)
                .frame(width: 30, height: 30)
                .background(Color.surfaceSecondary)
                .clipShape(Circle())
        }
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
                    .accessibilityIdentifier(busyAccessibilityIdentifier ?? "")
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
