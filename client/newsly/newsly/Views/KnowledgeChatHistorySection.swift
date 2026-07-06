//
//  KnowledgeChatHistorySection.swift
//  newsly
//

import SwiftUI

struct KnowledgeChatHistorySection: View {
    let viewModel: KnowledgeHubViewModel
    let onSelectSession: ((ChatSessionRoute) -> Void)?
    let chatTransitionNamespace: Namespace.ID?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionHeader("Recent Chats")

            if viewModel.isLoading && viewModel.sessions.isEmpty {
                loadingRow
            } else if viewModel.sessions.isEmpty {
                emptyRow
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(chatDayGroups) { group in
                        chatDayDelimiter(group.label)

                        ForEach(group.sessions) { session in
                            Button {
                                onSelectSession?(ChatSessionRoute(session: session))
                            } label: {
                                ChatSessionCard(session: session)
                            }
                            .buttonStyle(.plain)
                            .matchedContentZoomSource(id: session.id, namespace: chatTransitionNamespace)
                            .padding(.horizontal, Spacing.appHorizontalMargin)
                        }
                    }
                }

                footer
            }
        }
    }

    private struct ChatDayGroup: Identifiable {
        let id: String
        let label: String
        var sessions: [ChatSessionSummary]
    }

    private static let chatDayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private var chatDayGroups: [ChatDayGroup] {
        var groups: [ChatDayGroup] = []
        for session in viewModel.sessions {
            let label = chatDayLabel(for: session.lastActivityDate)
            if groups.last?.label == label {
                groups[groups.count - 1].sessions.append(session)
            } else {
                groups.append(ChatDayGroup(id: label, label: label, sessions: [session]))
            }
        }
        return groups
    }

    private func chatDayLabel(for date: Date?) -> String {
        guard let date else { return "EARLIER" }
        let calendar = Calendar.current

        if calendar.isDateInToday(date) {
            return "TODAY"
        } else if calendar.isDateInYesterday(date) {
            return "YESTERDAY"
        }
        return Self.chatDayFormatter.string(from: date).uppercased()
    }

    private func chatDayDelimiter(_ label: String) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .kicker(color: .sectionDelimiter)

            Rectangle()
                .fill(Color.outlineVariant)
                .frame(height: 1)
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 2)
    }

    private var loadingRow: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text("Loading chats")
                .font(.terracottaBodyMedium)
                .foregroundStyle(Color.onSurfaceSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 18)
        .padding(.horizontal, Spacing.appHorizontalMargin)
    }

    private var emptyRow: some View {
        Text("No chats yet")
            .font(.terracottaBodyMedium)
            .foregroundStyle(Color.onSurfaceSecondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 8)
    }

    private var footer: some View {
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
                .padding(.horizontal, Spacing.appHorizontalMargin)
            } else if viewModel.hasMoreSessions {
                Button {
                    Task { await viewModel.loadMoreSessions() }
                } label: {
                    Label("Load more", systemImage: "chevron.down")
                        .font(.terracottaBodyMedium.weight(.semibold))
                        .foregroundStyle(Color.terracottaPrimary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, Spacing.appHorizontalMargin)
            }
        }
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .kicker()
            .accessibilityLabel(title)
            .padding(.horizontal, Spacing.appHorizontalMargin)
    }
}
