//
//  KnowledgeView.swift
//  newsly
//
//  Created by Assistant on 11/28/25.
//

import SwiftUI

struct KnowledgeView: View {
    let onSelectSession: ((ChatSessionRoute) -> Void)?
    let onShowKnowledgeLibrary: (() -> Void)?

    @StateObject private var viewModel = KnowledgeHubViewModel()
    @ObservedObject private var settings = AppSettings.shared
    @State private var searchText = ""
    @FocusState private var isSearchFocused: Bool

    private let quickActions: [HubAction] = [
        HubAction(
            icon: "doc.text.magnifyingglass",
            title: "Today's Summary",
            subtitle: "Recap of the last day's content",
            run: { viewModel in await viewModel.startSummaryChat() }
        ),
        HubAction(
            icon: "bubble.left.and.text.bubble.right",
            title: "Top Comments",
            subtitle: "Most interesting discussions",
            run: { viewModel in await viewModel.startCommentsChat() }
        ),
    ]

    private let discoveryActions: [HubAction] = [
        HubAction(
            icon: "newspaper.fill",
            title: "Find New Articles",
            subtitle: "Fresh reads based on your history",
            run: { viewModel in await viewModel.startFindArticlesChat() }
        ),
        HubAction(
            icon: "dot.radiowaves.left.and.right",
            title: "Find New Feeds",
            subtitle: "Sources and podcasts to add next",
            run: { viewModel in await viewModel.startFindFeedsChat() }
        ),
    ]

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    init(
        onSelectSession: ((ChatSessionRoute) -> Void)? = nil,
        onShowKnowledgeLibrary: (() -> Void)? = nil
    ) {
        self.onSelectSession = onSelectSession
        self.onShowKnowledgeLibrary = onShowKnowledgeLibrary
    }

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            ScrollView {
                LazyVStack(spacing: 0) {
                    headerSection
                    searchFieldSection
                    errorBannerSection
                    quickActionsSection
                    librarySection
                    discoverySection
                    chatHistorySection
                }
                .padding(.bottom, 148)
            }

            newChatMicButton
                .padding(.trailing, 20)
                .padding(.bottom, 42)
        }
            .dynamicTypeSize(appTextSize)
            .background(Color.surfacePrimary.ignoresSafeArea())
            .navigationBarTitleDisplayMode(.inline)
            .task {
                await viewModel.loadHub()
            }
            .refreshable {
                await viewModel.loadHub()
            }
    }

    // MARK: - Header

    private var headerSection: some View {
        Text("Knowledge")
            .font(.terracottaDisplayLarge)
            .foregroundStyle(Color.onSurface)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Spacing.screenHorizontal)
            .padding(.top, 16)
            .padding(.bottom, 24)
    }

    // MARK: - Search Field

    private var searchFieldSection: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(.onSurfaceSecondary)

            TextField("Ask anything...", text: $searchText)
                .font(.terracottaBodyLarge)
                .focused($isSearchFocused)
                .submitLabel(.send)
                .onSubmit {
                    sendSearchQuery()
                }

            if !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Button {
                    sendSearchQuery()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 28))
                        .foregroundColor(viewModel.isCreatingSession ? .onSurfaceSecondary : .terracottaPrimary)
                }
                .disabled(viewModel.isCreatingSession)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Color.surfaceContainer)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .padding(.horizontal, Spacing.screenHorizontal)
        .padding(.bottom, 24)
    }

    private var errorBannerSection: some View {
        Group {
            if let errorMessage = viewModel.errorMessage {
                Text(errorMessage)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Color.red.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 16))
                    .padding(.horizontal, Spacing.screenHorizontal)
                    .padding(.bottom, 24)
            }
        }
    }

    // MARK: - Quick Actions

    private var quickActionsSection: some View {
        actionSection(title: "Quick Actions", actions: quickActions)
    }

    private var librarySection: some View {
        Group {
            if let onShowKnowledgeLibrary {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Library")
                        .font(.terracottaHeadlineSmall)
                        .foregroundStyle(Color.onSurface)
                        .padding(.horizontal, Spacing.screenHorizontal)

                    Button {
                        onShowKnowledgeLibrary()
                    } label: {
                        HStack(spacing: 14) {
                            Image(systemName: "books.vertical.fill")
                                .font(.system(size: 18, weight: .semibold))
                                .foregroundColor(.terracottaPrimary)
                                .frame(width: 38, height: 38)
                                .background(Color.terracottaPrimary.opacity(0.14))
                                .clipShape(RoundedRectangle(cornerRadius: 12))

                            VStack(alignment: .leading, spacing: 4) {
                                Text("Knowledge Library")
                                    .font(.terracottaHeadlineSmall)
                                    .foregroundColor(.onSurface)

                                Text("Saved articles and podcasts with markdown ready")
                                    .font(.terracottaBodySmall)
                                    .foregroundColor(.onSurfaceSecondary)
                            }

                            Spacer()

                            Image(systemName: "arrow.right")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundColor(.onSurfaceSecondary)
                        }
                        .padding(14)
                        .background(Color.surfaceSecondary)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                        .overlay(
                            RoundedRectangle(cornerRadius: 16)
                                .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
                        )
                        .padding(.horizontal, Spacing.screenHorizontal)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.bottom, 28)
            }
        }
    }

    private var discoverySection: some View {
        actionSection(title: "Discover", actions: discoveryActions)
    }

    private func actionSection(title: String, actions: [HubAction]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
                .padding(.horizontal, Spacing.screenHorizontal)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 12) {
                    ForEach(actions) { action in
                        Button {
                            startAction(action)
                        } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                Image(systemName: action.icon)
                                    .font(.system(size: 22))
                                    .foregroundColor(.terracottaPrimary)

                                Text(action.title)
                                    .font(.terracottaHeadlineSmall)
                                    .foregroundColor(.onSurface)
                                    .lineLimit(2)

                                Text(action.subtitle)
                                    .font(.terracottaBodySmall)
                                    .foregroundColor(.onSurfaceSecondary)
                                    .lineLimit(2)
                            }
                            .frame(width: 184, alignment: .leading)
                            .padding(14)
                            .background(Color.surfaceSecondary)
                            .clipShape(RoundedRectangle(cornerRadius: 16))
                            .overlay(
                                RoundedRectangle(cornerRadius: 16)
                                    .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
                            )
                        }
                        .buttonStyle(.plain)
                        .disabled(viewModel.isCreatingSession)
                    }
                }
                .padding(.horizontal, Spacing.screenHorizontal)
            }
        }
        .padding(.bottom, 28)
    }

    private func startAction(_ action: HubAction) {
        Task {
            if let route = await action.run(viewModel) {
                onSelectSession?(route)
            }
        }
    }

    // MARK: - Chat History

    private var chatHistorySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Chat History")
                .font(.terracottaHeadlineSmall)
                .foregroundStyle(Color.onSurface)
                .padding(.horizontal, Spacing.screenHorizontal)

            if viewModel.isLoading && viewModel.sessions.isEmpty {
                chatHistoryLoadingRow
            } else if viewModel.sessions.isEmpty {
                chatHistoryEmptyRow
            } else {
                VStack(spacing: 10) {
                    ForEach(viewModel.sessions) { session in
                        Button {
                            onSelectSession?(ChatSessionRoute(session: session))
                        } label: {
                            ChatSessionCard(session: session)
                        }
                        .buttonStyle(.plain)
                        .padding(.horizontal, Spacing.screenHorizontal)
                        .task {
                            await viewModel.loadMoreSessionsIfNeeded(currentSession: session)
                        }
                    }
                }

                chatHistoryFooter
            }
        }
        .padding(.bottom, 32)
    }

    private var chatHistoryLoadingRow: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text("Loading chats")
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 18)
        .padding(.horizontal, Spacing.screenHorizontal)
    }

    private var chatHistoryEmptyRow: some View {
        Text("No chats yet")
            .font(.terracottaBodyMedium)
            .foregroundStyle(Color.onSurfaceSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Spacing.screenHorizontal)
            .padding(.vertical, 8)
    }

    private var chatHistoryFooter: some View {
        Group {
            if viewModel.isLoadingMore {
                ProgressView()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            } else if viewModel.hasLoadMoreError {
                Button {
                    Task { await viewModel.loadMoreSessions() }
                } label: {
                    Label("Retry", systemImage: "arrow.clockwise")
                        .font(.terracottaBodyMedium)
                        .foregroundStyle(Color.terracottaPrimary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, Spacing.screenHorizontal)
            }
        }
    }

    // MARK: - Actions

    private var newChatMicButton: some View {
        TapToTalkMicButton(
            isEnabled: !viewModel.isCreatingSession,
            isRecording: false,
            isBusy: viewModel.isCreatingSession,
            size: 60,
            action: {
                Task {
                    if let route = await viewModel.startNewChat() {
                        onSelectSession?(route)
                    }
                }
            }
        )
        .shadow(color: .black.opacity(0.22), radius: 12, y: 8)
        .accessibilityIdentifier("knowledge.new_chat_mic")
        .accessibilityLabel("New chat")
        .accessibilityHint("Start a new chat session")
    }

    private func sendSearchQuery() {
        let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
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
}

private struct HubAction: Identifiable {
    let id = UUID()
    let icon: String
    let title: String
    let subtitle: String
    let run: @MainActor (KnowledgeHubViewModel) async -> ChatSessionRoute?
}

#Preview {
    KnowledgeView()
}
