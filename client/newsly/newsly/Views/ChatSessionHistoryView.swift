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
                BuddyLoadingView()
            } else if let error = viewModel.errorMessage, knowledgeSessions.isEmpty {
                ErrorView(message: error) {
                    Task { await viewModel.loadSessions() }
                }
            } else if knowledgeSessions.isEmpty {
                emptyStateView
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        SearchBar(
                            placeholder: "Search history...",
                            text: $searchText
                        )
                        .padding(.horizontal, Spacing.appHorizontalMargin)
                        .padding(.bottom, 4)

                        // The same rows and day groups as the Knowledge list.
                        ForEach(ChatHistoryDayGroup.group(filteredSessions)) { group in
                            KnowledgeDayDelimiter(title: group.title)
                            ForEach(Array(group.sessions.enumerated()), id: \.element.id) { index, session in
                                if index > 0 {
                                    KnowledgeTimelineRowDivider()
                                }
                                Button {
                                    onSelectSession(ChatSessionRoute(session: session))
                                } label: {
                                    KnowledgeChatRow(
                                        session: session,
                                        activityDate: ChatHistoryDayGroup.activityDate(of: session),
                                        preview: KnowledgeChatPreview.make(for: session)
                                    )
                                }
                                .buttonStyle(.plain)
                                .matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)
                                .contextMenu {
                                    Button(role: .destructive) {
                                        Task { await viewModel.deleteSessions(ids: [session.id]) }
                                    } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                                .accessibilityIdentifier("chat.history.session.\(session.id)")
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
        .background(Color.surfacePrimary.ignoresSafeArea())
        .extendsToOpenDisplayEdge()
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
                .foregroundStyle(Color.brandPrimary)

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

/// Chat history grouped by day of last activity, with the Knowledge list's
/// "TODAY" / "YESTERDAY" / weekday titles.
struct ChatHistoryDayGroup: Identifiable {
    let day: Date
    let title: String
    let sessions: [ChatSessionSummary]

    var id: Date { day }

    /// The same date the Knowledge list files a chat under.
    static func activityDate(of session: ChatSessionSummary) -> Date {
        session.lastActivityDate
    }

    static func group(_ sessions: [ChatSessionSummary], calendar: Calendar = .current) -> [Self] {
        Dictionary(grouping: sessions) { calendar.startOfDay(for: activityDate(of: $0)) }
            .map { day, sessions in
                Self(
                    day: day,
                    title: KnowledgeTimelineDayGroup.title(for: day, calendar: calendar),
                    sessions: sessions.sorted { activityDate(of: $0) > activityDate(of: $1) }
                )
            }
            .sorted { $0.day > $1.day }
    }
}
