//
//  ChatSessionHistoryView.swift
//  newsly
//

import SwiftUI

struct ChatSessionHistoryView: View {
    let onSelectSession: (ChatSessionRoute) -> Void
    let chatTransitionNamespace: Namespace.ID?

    @State private var viewModel: ChatSessionsViewModel
    @State private var searchText = ""

    init(
        onSelectSession: @escaping (ChatSessionRoute) -> Void,
        viewModel: ChatSessionsViewModel,
        chatTransitionNamespace: Namespace.ID? = nil
    ) {
        self.onSelectSession = onSelectSession
        self.chatTransitionNamespace = chatTransitionNamespace
        self._viewModel = State(initialValue: viewModel)
    }

    private var knowledgeSessions: [ChatSessionSummary] {
        viewModel.sessions
    }

    private var filteredSessions: [ChatSessionSummary] {
        let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return knowledgeSessions }
        return knowledgeSessions.filter { session in
            let haystacks = [
                session.displayTitle,
                session.displaySubtitle ?? "",
                session.articleTitle ?? "",
                session.articleSource ?? "",
                session.topic ?? ""
            ]
            return haystacks.contains { $0.localizedCaseInsensitiveContains(trimmed) }
        }
    }

    private var shouldShowNoResults: Bool {
        let trimmed = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        return !trimmed.isEmpty && filteredSessions.isEmpty
    }

    var body: some View {
        Group {
            if viewModel.isLoading && knowledgeSessions.isEmpty {
                LoadingView()
            } else if let error = viewModel.errorMessage, knowledgeSessions.isEmpty {
                ErrorView(message: error) {
                    Task { await viewModel.loadSessions() }
                }
            } else if knowledgeSessions.isEmpty {
                emptyStateView
            } else {
                ScrollView {
                    LazyVStack(spacing: 12) {
                        SearchBar(
                            placeholder: "Search history...",
                            text: $searchText
                        )
                        .padding(.horizontal, Spacing.appHorizontalMargin)

                        ForEach(filteredSessions) { session in
                            Button {
                                onSelectSession(ChatSessionRoute(session: session))
                            } label: {
                                ChatSessionCard(session: session)
                            }
                            .buttonStyle(.plain)
                            .matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                            .contextMenu {
                                Button(role: .destructive) {
                                    Task { await viewModel.deleteSessions(ids: [session.id]) }
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                        }

                        if shouldShowNoResults {
                            noResultsRow
                        }
                    }
                    .padding(.vertical, 8)
                }
                .topScreenEdgeFade()
                .refreshable {
                    await viewModel.loadSessions()
                }
            }
        }
        .background(Color.surfacePrimary.ignoresSafeArea())
        .navigationTitle("Chat History")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.loadSessions()
        }
    }

    private var emptyStateView: some View {
        VStack(spacing: 20) {
            Image(systemName: "brain.head.profile")
                .font(.appSymbol(size: 48, weight: .light))
                .foregroundStyle(Color.brandPrimary.opacity(0.7))

            VStack(spacing: 6) {
                Text("No chats yet")
                    .font(.terracottaHeadlineMedium)
                    .foregroundStyle(Color.onSurface)

                Text("Start a new chat from the Knowledge hub.")
                    .font(.terracottaBodyMedium)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .multilineTextAlignment(.center)
            .frame(maxWidth: 280)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }

    private var noResultsRow: some View {
        VStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.appSymbol(size: Spacing.iconSize))
                .foregroundStyle(Color.onSurfaceSecondary)
            Text("No matching chats")
                .font(.terracottaHeadlineSmall)
                .fontWeight(.semibold)
            Text("Try a different keyword.")
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Spacing.sectionTop)
    }
}
