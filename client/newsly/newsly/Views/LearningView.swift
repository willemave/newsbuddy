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
        chatTransitionNamespace: Namespace.ID? = nil
    ) {
        self.focusRequest = focusRequest
        self.onFocusHandled = onFocusHandled
        self.onSelectSession = onSelectSession
        self.onOpenMore = onOpenMore
        self.viewModel = viewModel
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

    private var timelineSections: [LearningTimelineSection] {
        LearningTimelineGrouper.sections(for: timeline)
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                EditorialMastheadHeader(
                    title: "Learning",
                    trailingAccessory: onOpenMore.map { action in
                        AnyView(moreMenuButton(action))
                    }
                )

                composer
                    .padding(.bottom, 24)

                timelineContent
            }
            .padding(.bottom, 32)
        }
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
            .ignoresSafeArea()
        }
        .task {
            await loadLearningScreen()
            await viewModel.checkAndRefreshVoiceDictation()
            handleFocusRequest(focusRequest)
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
                            viewModel.isCreatingSession ? Color.onSurfaceSecondary : Color.terracottaPrimary
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
        .background(Color.surfaceContainer)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
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
        } else if timeline.isEmpty {
            EmptyStateView(
                icon: "sparkles",
                title: "Start learning",
                subtitle: "Chats, Learning Decks, and narrations will appear together here."
            )
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 28)
        } else {
            ForEach(Array(timelineSections.enumerated()), id: \.element.id) { sectionIndex, section in
                dayDivider(section.label, isFirst: sectionIndex == 0)

                ForEach(section.items) { item in
                    timelineRow(item)
                }
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
        case .deck(let deck):
            Button {
                Task { await openDeck(deck) }
            } label: {
                LearningDeckTimelineRow(deck: deck, activityDate: item.activityDate)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("learning.deck.\(deck.id)")
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
        }
    }

    private func dayDivider(_ label: String, isFirst: Bool) -> some View {
        HStack(spacing: 10) {
            Text(isFirst ? "CONTINUE · \(label)" : label)
                .kicker(color: .sectionDelimiter)
            Rectangle().fill(Color.outlineVariant).frame(height: 1)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 6)
        .padding(.bottom, 2)
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

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            LearningArtwork(
                imageUrl: session.articleImageUrl,
                thumbnailUrl: session.articleThumbnailUrl,
                fallbackIcon: "bubble.left.and.bubble.right"
            )

            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text("CHAT").kicker(color: .terracottaPrimary)
                    Text("· \(ContentTimestampFormatter.compactRelativeText(from: activityDate))")
                        .font(.terracottaLabelSmall)
                        .foregroundStyle(Color.onSurfaceTertiary)
                    if session.isProcessing {
                        ProgressView().controlSize(.mini)
                    }
                }
                Text(session.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(3)
                    .truncationMode(.tail)
                if let preview {
                    Text(preview)
                        .font(.terracottaBodySmall)
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Image(systemName: "chevron.right")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
                .padding(.top, 4)
        }
        .learningTimelineRow()
    }
}

private struct LearningDeckTimelineRow: View {
    let deck: LearningDeck
    let activityDate: Date

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            LearningArtwork(imageUrl: nil, thumbnailUrl: nil, fallbackIcon: "rectangle.stack.fill", isDeck: true)
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text("LEARNING DECK").kicker(color: .terracottaPrimary)
                    Text("· \(ContentTimestampFormatter.compactRelativeText(from: activityDate))")
                        .font(.terracottaLabelSmall)
                        .foregroundStyle(Color.onSurfaceTertiary)
                    Text(deck.statusLabel.uppercased())
                        .font(.terracottaLabelSmall)
                        .foregroundStyle(Color.onSurfaceTertiary)
                }
                Text(deck.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(3)
                    .truncationMode(.tail)
                Text(deck.timelineSubtitle)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: "chevron.right")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.onSurfaceTertiary)
                .padding(.top, 4)
        }
        .learningTimelineRow()
    }
}

private struct LearningNarrationRow: View {
    let episode: AudioEpisode
    let activityDate: Date
    let subtitle: String
    let isPlaying: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            LearningArtwork(imageUrl: nil, thumbnailUrl: nil, fallbackIcon: "waveform")
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text("NARRATION").kicker(color: .terracottaPrimary)
                    Text("· \(ContentTimestampFormatter.compactRelativeText(from: activityDate))")
                        .font(.terracottaLabelSmall)
                        .foregroundStyle(Color.onSurfaceTertiary)
                }
                Text(episode.title)
                    .font(.terracottaHeadlineSmall)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(3)
                    .truncationMode(.tail)
                Text(subtitle)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                .font(.appSymbol(size: 11, weight: .semibold))
                .foregroundStyle(Color.terracottaPrimary)
                .frame(width: 30, height: 30)
                .background(Color.surfaceSecondary)
                .clipShape(Circle())
        }
        .learningTimelineRow()
    }
}

private struct LearningArtwork: View {
    let imageUrl: String?
    let thumbnailUrl: String?
    let fallbackIcon: String
    var isDeck = false

    private let size = CGSize(width: 72, height: 64)

    var body: some View {
        CachedAsyncImage(
            url: imageUrl.flatMap(ServerImageURL.resolve),
            thumbnailUrl: thumbnailUrl.flatMap(ServerImageURL.resolve),
            targetSize: size
        ) { image in
            image.resizable().aspectRatio(contentMode: .fill)
                .frame(width: size.width, height: size.height).clipped()
        } placeholder: {
            ZStack {
                if isDeck {
                    Color.surfaceSecondary
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(Color.terracottaPrimary.opacity(0.45), lineWidth: 1)
                        .frame(width: 38, height: 27)
                        .offset(x: 5, y: 4)
                } else {
                    Color.surfaceSecondary
                }
                Image(systemName: fallbackIcon)
                    .font(.appSymbol(size: 17, weight: .semibold))
                    .foregroundStyle(Color.terracottaPrimary)
            }
            .frame(width: size.width, height: size.height)
        }
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.45), lineWidth: 0.5)
        }
    }
}

private extension View {
    func learningTimelineRow() -> some View {
        self
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 11)
            .contentShape(Rectangle())
    }
}
