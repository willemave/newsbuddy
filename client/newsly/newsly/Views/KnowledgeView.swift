//
//  KnowledgeView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI

enum KnowledgeFocusTarget: Hashable {
    case narrations
}

struct KnowledgeFocusRequest: Equatable {
    let id = UUID()
    let target: KnowledgeFocusTarget
}

private enum KnowledgeSheetDestination: String, Identifiable {
    case narrationList
    case learningDeckList

    var id: String { rawValue }
}

struct KnowledgeView: View {
    let focusRequest: KnowledgeFocusRequest?
    let onFocusHandled: ((KnowledgeFocusRequest) -> Void)?
    let onSelectSession: ((ChatSessionRoute) -> Void)?
    let onShowKnowledgeLibrary: (() -> Void)?
    let chatTransitionNamespace: Namespace.ID?
    var onOpenMore: (() -> Void)? = nil

    @State private var viewModel: KnowledgeHubViewModel
    @State private var customNarrationLibrary: CustomNarrationLibraryViewModel
    @State private var learningDecksViewModel = RootDependencyFactory.makeLearningDecksViewModel()
    @State private var settings = AppSettings.shared
    @State private var searchText = ""
    @State private var activeSheet: KnowledgeSheetDestination?
    @State private var runningActionID: HubActionID?
    @FocusState private var isSearchFocused: Bool

    init(
        focusRequest: KnowledgeFocusRequest? = nil,
        onFocusHandled: ((KnowledgeFocusRequest) -> Void)? = nil,
        onSelectSession: ((ChatSessionRoute) -> Void)? = nil,
        onShowKnowledgeLibrary: (() -> Void)? = nil,
        onOpenMore: (() -> Void)? = nil,
        viewModel: KnowledgeHubViewModel? = nil,
        readStateCache: ReadStateCache? = nil,
        chatTransitionNamespace: Namespace.ID? = nil
    ) {
        let readStateCache = readStateCache ?? ReadStateCache()
        self.focusRequest = focusRequest
        self.onFocusHandled = onFocusHandled
        self.onSelectSession = onSelectSession
        self.onShowKnowledgeLibrary = onShowKnowledgeLibrary
        self.onOpenMore = onOpenMore
        self.chatTransitionNamespace = chatTransitionNamespace
        self._viewModel = State(
            initialValue: viewModel ?? RootDependencyFactory.makeKnowledgeHubViewModel()
        )
        self._customNarrationLibrary = State(
            initialValue: RootDependencyFactory.makeCustomNarrationLibraryViewModel(readStateCache: readStateCache)
        )
    }

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    var body: some View {
        ScrollViewReader { scrollProxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    headerSection
                    searchFieldSection
                    errorBannerSection
                    KnowledgeLibrarySection(
                        onShowKnowledgeLibrary: onShowKnowledgeLibrary,
                        onShowNarrations: { activeSheet = .narrationList },
                        onShowLearningDecks: { activeSheet = .learningDeckList }
                    )
                        .id(KnowledgeFocusTarget.narrations)
                    KnowledgeActionsSection(
                        viewModel: viewModel,
                        runningActionID: $runningActionID,
                        onSelectSession: onSelectSession
                    )
                    KnowledgeChatHistorySection(
                        viewModel: viewModel,
                        onSelectSession: onSelectSession,
                        chatTransitionNamespace: chatTransitionNamespace
                    )
                }
                .padding(.bottom, 32)
            }
            .accessibilityIdentifier("knowledge.screen")
            .refreshable {
                await loadKnowledgeScreen()
            }
            .onAppear {
                scrollToFocusRequest(focusRequest, proxy: scrollProxy)
            }
            .onChange(of: focusRequest) { _, request in
                scrollToFocusRequest(request, proxy: scrollProxy)
            }
        }
        .topScreenEdgeFade()
        .dynamicTypeSize(appTextSize)
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $activeSheet) { destination in
            switch destination {
            case .narrationList:
                CustomNarrationListSheet(
                    viewModel: customNarrationLibrary,
                    playbackService: customNarrationLibrary.playbackService
                )
            case .learningDeckList:
                LearningDeckListSheet(viewModel: learningDecksViewModel)
            }
        }
        .onChange(of: viewModel.completedVoiceRoute) { _, route in
            guard let route else { return }
            viewModel.clearCompletedVoiceRoute()
            onSelectSession?(route)
        }
        .task {
            await loadKnowledgeScreen()
            await viewModel.checkAndRefreshVoiceDictation()
        }
        .onDisappear {
            viewModel.cancelVoiceRecording()
        }
    }

    // MARK: - Header

    private var headerSection: some View {
        EditorialMastheadHeader(
            title: "Knowledge",
            trailingAccessory: onOpenMore.map { action in AnyView(moreMenuButton(action)) }
        )
    }

    private func moreMenuButton(_ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: "line.3.horizontal")
                .font(.appSymbol(size: 26, weight: .semibold))
                .frame(width: 48, height: 48, alignment: .trailing)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.onSurface)
        .accessibilityLabel("Settings and more")
        .accessibilityIdentifier("knowledge.more_menu")
    }

    // MARK: - Search Field

    private var trimmedSearchText: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var searchFieldSection: some View {
        HStack(spacing: 10) {
            Image(systemName: "text.bubble")
                .font(.appSymbol(size: 16, weight: .medium))
                .foregroundColor(.onSurfaceSecondary)
                .accessibilityHidden(true)

            TextField("Ask anything...", text: $searchText)
                .font(.terracottaBodyLarge)
                .focused($isSearchFocused)
                .submitLabel(.send)
                .onSubmit {
                    sendSearchQuery()
                }
                .padding(.vertical, 11)
                .padding(.horizontal, 4)
                .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                .background(Color.surfaceContainer.opacity(0.001))
                .accessibilityLabel("Ask Knowledge")
                .accessibilityIdentifier("knowledge.search.input")

            if !trimmedSearchText.isEmpty && !viewModel.isVoiceRecording {
                Button {
                    sendSearchQuery()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.appSymbol(size: 28))
                        .foregroundColor(viewModel.isCreatingSession ? .onSurfaceSecondary : .terracottaPrimary)
                        .frame(width: 44, height: 44)
                }
                .disabled(viewModel.isCreatingSession)
                .contentShape(Circle())
                .accessibilityLabel("Send question")
                .accessibilityIdentifier("knowledge.search.send")
            } else {
                composerMicButton
            }
        }
        .padding(.leading, 16)
        .padding(.trailing, 8)
        .background(Color.surfaceContainer)
        .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.bottom, 24)
    }

    private var composerMicButton: some View {
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
                        onSelectSession?(route)
                    }
                }
            }
        )
        .frame(width: 44, height: 44)
        .opacity(viewModel.voiceDictationAvailable || viewModel.isVoiceRecording ? 1 : 0.72)
        .accessibilityIdentifier("knowledge.new_chat_mic")
        .accessibilityLabel(viewModel.isVoiceRecording ? "Stop voice chat" : "Start voice chat")
        .accessibilityHint(viewModel.isVoiceRecording ? "Stop recording and start a chat" : "Record a question and start a chat")
    }

    private var errorBannerSection: some View {
        Group {
            if let errorMessage = viewModel.errorMessage {
                Text(errorMessage)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.statusDestructive)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Color.statusDestructive.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous))
                    .padding(.horizontal, Spacing.appHorizontalMargin)
                    .padding(.bottom, 24)
            }
        }
    }

    private func scrollToFocusRequest(
        _ request: KnowledgeFocusRequest?,
        proxy: ScrollViewProxy
    ) {
        guard let request else { return }

        Task { @MainActor in
            await Task.yield()
            withAnimation(AppMotion.subtle) {
                proxy.scrollTo(request.target, anchor: .top)
            }
            if request.target == .narrations {
                activeSheet = .narrationList
            }
            onFocusHandled?(request)
        }
    }

    private func sendSearchQuery() {
        let trimmed = trimmedSearchText
        guard !trimmed.isEmpty else { return }

        isSearchFocused = false
        let query = trimmed
        searchText = ""

        Task {
            if let route = await viewModel.startSearchChat(message: query) {
                onSelectSession?(route)
            }
        }
    }

    @MainActor
    private func loadKnowledgeScreen() async {
        await viewModel.loadHub()
        await customNarrationLibrary.load()
        await learningDecksViewModel.load()
    }
}

#Preview {
    KnowledgeView()
}
