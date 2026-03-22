//
//  ChatSessionHistoryView.swift
//  newsly
//

import SwiftUI

struct ChatSessionHistoryView: View {
    let onSelectSession: (ChatSessionRoute) -> Void

    @StateObject private var viewModel = ChatSessionsViewModel()
    @ObservedObject private var settings = AppSettings.shared
    @State private var searchText = ""

    private var appTextSize: DynamicTypeSize {
        AppTextSize(index: settings.appTextSizeIndex).dynamicTypeSize
    }

    private var knowledgeSessions: [ChatSessionSummary] {
        viewModel.sessions.filter {
            $0.sessionType != "voice_live" && !$0.isLiveVoiceSession
        }
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
                        .padding(.horizontal, 16)

                        ForEach(filteredSessions) { session in
                            Button {
                                onSelectSession(ChatSessionRoute(sessionId: session.id))
                            } label: {
                                ChatSessionCard(session: session)
                            }
                            .buttonStyle(.plain)
                            .padding(.horizontal, 16)
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
                .refreshable {
                    await viewModel.loadSessions()
                }
            }
        }
        .dynamicTypeSize(appTextSize)
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
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(Color.terracottaPrimary.opacity(0.7))

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
                .font(.system(size: Spacing.iconSize))
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
