//
//  ShortFormView.swift
//  newsly
//
//  Created by Assistant on 11/4/25.
//

import os.log
import SwiftUI

private let logger = Logger(subsystem: "com.newsly", category: "ShortFormView")

struct ShortFormView: View {
    @ObservedObject var viewModel: ShortNewsListViewModel
    let onSelect: (ContentDetailRoute) -> Void
    @StateObject private var processingCountService = ProcessingCountService.shared
    private let chatService = ChatService.shared

    /// Track which items have already been marked as read to avoid duplicates
    @State private var markedAsReadIds: Set<Int> = []
    @State private var showMarkAllConfirmation = false
    @State private var topVisibleItemId: Int?
    @State private var quickActionErrorMessage: String?
    @State private var activeQuickActionId: String?

    var body: some View {
        let items = viewModel.currentItems()
        let isEmpty = items.isEmpty
        let hasUnreadItems = items.contains(where: { !$0.isRead })

        ScrollView {
            LazyVStack(spacing: 0) {
                if case .error(let error) = viewModel.state, isEmpty {
                    ErrorView(message: error.localizedDescription) {
                        viewModel.refreshTrigger.send(())
                    }
                    .padding(.top, 48)
                } else if viewModel.state == .initialLoading, isEmpty {
                    ProgressView("Loading")
                        .padding(.top, 48)
                } else if isEmpty {
                    shortFormEmptyState
                } else {
                    Text("Fast read")
                        .font(.terracottaDisplayLarge)
                        .foregroundStyle(Color.onSurface)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Spacing.screenHorizontal)
                        .padding(.top, 16)
                        .padding(.bottom, 24)

                    shortNewsQuickActions(items: items)
                        .padding(.bottom, 20)

                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        // Day delimiter: show when this item starts a new day
                        if index == 0 || item.calendarDayKey != items[index - 1].calendarDayKey {
                            DayDelimiter(item: item, isFirst: index == 0)
                                .equatable()
                        }

                        ShortNewsRow(item: item)
                            .equatable()
                            .accessibilityIdentifier("short.row.\(item.id)")
                            .id(item.id)
                            .onTapGesture {
                                let ids = items.map(\.id)
                                let route = ContentDetailRoute(
                                    contentId: item.id,
                                    contentType: item.contentTypeEnum ?? .news,
                                    allContentIds: ids
                                )
                                onSelect(route)
                            }
                            .onAppear {
                                if item.id == items.last?.id {
                                    viewModel.loadMoreTrigger.send(())
                                }
                            }
                    }

                    if hasUnreadItems {
                        Button {
                            showMarkAllConfirmation = true
                        } label: {
                            Text("Mark All as Read")
                                .font(.subheadline.weight(.semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 12)
                                .background(Color.surfaceSecondary)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                        }
                        .buttonStyle(.plain)
                        .padding(.horizontal, Spacing.screenHorizontal)
                        .padding(.vertical, 8)
                    }

                    if viewModel.state == .loadingMore {
                        ProgressView()
                            .padding(.vertical, 16)
                    }
                }
            }
            .scrollTargetLayout()
        }
        .accessibilityIdentifier("short.screen")
        .screenContainer()
        .onScrollTargetVisibilityChange(idType: Int.self) { visibleIds in
            topVisibleItemId = visibleIds.first
        }
        .onChange(of: topVisibleItemId) { _, _ in
            markItemsAboveAsRead()
        }
        .onScrollPhaseChange { _, newPhase in
            guard newPhase == .idle else { return }
            markItemsAboveAsRead()
        }
        .refreshable {
            viewModel.refreshTrigger.send(())
            await processingCountService.refreshCount()
        }
        .onAppear {
            if viewModel.currentItems().isEmpty {
                viewModel.refreshTrigger.send(())
            }
            Task {
                await processingCountService.refreshCount()
            }
        }
        .confirmationDialog(
            "Mark all news items as read?",
            isPresented: $showMarkAllConfirmation
        ) {
            Button("Mark All as Read", role: .destructive) {
                showMarkAllConfirmation = false
                viewModel.markAllVisibleAsRead()
            }
            Button("Cancel", role: .cancel) {
                showMarkAllConfirmation = false
            }
        } message: {
            Text("Marks every unread item currently loaded in the list.")
        }
    }

    private func markItemsAboveAsRead() {
        guard let topVisibleItemId else { return }
        let items = viewModel.currentItems()
        guard let index = items.firstIndex(where: { $0.id == topVisibleItemId }) else { return }

        let idsToMark = items.prefix(index)
            .filter { !$0.isRead && !markedAsReadIds.contains($0.id) }
            .map(\.id)

        guard !idsToMark.isEmpty else { return }

        logger.info("[ShortFormView] Items scrolled past top | ids=\(idsToMark, privacy: .public)")
        idsToMark.forEach { markedAsReadIds.insert($0) }
        viewModel.itemsScrolledPastTop(ids: idsToMark)
    }

    @ViewBuilder
    private func shortNewsQuickActions(items: [ContentSummary]) -> some View {
        let quickActions = makeQuickActions(items: items)

        VStack(alignment: .leading, spacing: 10) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(quickActions) { action in
                        Button {
                            startQuickAction(action)
                        } label: {
                            ShortNewsQuickActionChip(
                                action: action,
                                isLoading: activeQuickActionId == action.id
                            )
                        }
                        .buttonStyle(.plain)
                        .disabled(activeQuickActionId != nil)
                        .accessibilityIdentifier("short.quick_action.\(action.id)")
                    }
                }
                .padding(.horizontal, Spacing.screenHorizontal)
            }

            if let quickActionErrorMessage {
                Text(quickActionErrorMessage)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(.red)
                    .padding(.horizontal, Spacing.screenHorizontal)
            }
        }
    }

    private func makeQuickActions(items: [ContentSummary]) -> [ShortNewsQuickAction] {
        let visibleItemIds = Array(items.prefix(15).map(\.id))

        return [
            ShortNewsQuickAction(
                id: "summarize_top_15",
                title: "Summarize Top 15",
                systemImage: "text.alignleft",
                prompt: "Summarize the top 15 news items in my short news feed right now.",
                screenContext: AssistantScreenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast read",
                    visibleContentIds: visibleItemIds,
                    query: "top 15 news items in my short news feed",
                    note: "Summarize the most important items from the fast news feed. Prefer the in-app short news feed over web search."
                )
            ),
            ShortNewsQuickAction(
                id: "latest_news",
                title: "What's Latest",
                systemImage: "clock.arrow.trianglehead.counterclockwise.rotate.90",
                prompt: "What's the latest news in my short news feed right now?",
                screenContext: AssistantScreenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast read",
                    visibleContentIds: visibleItemIds,
                    query: "latest news in my short news feed",
                    note: "Focus on the newest important developments from the fast news feed."
                )
            ),
            ShortNewsQuickAction(
                id: "spicy_discussions",
                title: "Spicy Discussions",
                systemImage: "flame",
                prompt: "What are the spiciest discussions in my short news feed right now?",
                screenContext: AssistantScreenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast read",
                    visibleContentIds: visibleItemIds,
                    query: "spiciest discussions in my short news feed",
                    note: "Pull out the sharpest disagreements, surprising takes, and most interesting discussion threads from the fast news feed."
                )
            ),
        ]
    }

    private func startQuickAction(_ action: ShortNewsQuickAction) {
        guard activeQuickActionId == nil else { return }

        activeQuickActionId = action.id
        quickActionErrorMessage = nil

        Task { @MainActor in
            defer { activeQuickActionId = nil }

            do {
                let response = try await chatService.createAssistantTurn(
                    message: action.prompt,
                    screenContext: action.screenContext
                )
                ChatNavigationCoordinator.shared.open(
                    ChatSessionRoute(
                        sessionId: response.session.id,
                        initialUserMessageText: response.userMessage.content,
                        initialUserMessageTimestamp: response.userMessage.timestamp,
                        pendingMessageId: response.messageId
                    )
                )
            } catch {
                quickActionErrorMessage = error.localizedDescription
            }
        }
    }

    @ViewBuilder
    private var shortFormEmptyState: some View {
        if processingCountService.newsProcessingCount > 0 {
            VStack(spacing: 16) {
                ProgressView()
                Text("Preparing \(processingCountService.newsProcessingCount) short-form items")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .containerRelativeFrame(.vertical)
        } else {
            EmptyStateView(
                icon: "bolt.fill",
                title: "No Short-Form Content",
                subtitle: "News items will appear here once processed"
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .containerRelativeFrame(.vertical)
        }
    }
}

private struct ShortNewsQuickAction: Identifiable {
    let id: String
    let title: String
    let systemImage: String
    let prompt: String
    let screenContext: AssistantScreenContext
}

private struct ShortNewsQuickActionChip: View {
    let action: ShortNewsQuickAction
    let isLoading: Bool

    var body: some View {
        HStack(spacing: 8) {
            if isLoading {
                ProgressView()
                    .controlSize(.small)
                    .tint(Color.terracottaPrimary)
            } else {
                Image(systemName: action.systemImage)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color.terracottaPrimary)
            }

            Text(action.title)
                .font(.terracottaBodyMedium.weight(.semibold))
                .foregroundStyle(Color.onSurface)
                .lineLimit(1)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.surfaceSecondary)
        .clipShape(Capsule())
        .overlay {
            Capsule()
                .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
        }
    }
}

// MARK: - Short News Row

private struct ShortNewsRow: View, Equatable {
    let item: ContentSummary

    private var titleWeight: Font.Weight {
        .regular
    }

    private var titleColor: Color {
        item.isRead ? .secondary : .primary
    }

    private var hasPlatform: Bool {
        item.platform?.isEmpty == false
    }

    /// Text-only metadata parts (platform, source, time) joined by " · ".
    private var metadataTextParts: [String] {
        var parts: [String] = []
        if let platform = item.platform, !platform.isEmpty {
            parts.append(platform.uppercased())
        }
        if let source = item.source, !source.isEmpty,
           source.caseInsensitiveCompare(item.platform ?? "") != .orderedSame {
            parts.append(source.uppercased())
        }
        if let time = item.relativeTimeDisplay {
            parts.append(time.uppercased())
        }
        return parts
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Headline
            Text(item.displayTitle)
                .font(.feedHeadline)
                .fontWeight(titleWeight)
                .foregroundColor(titleColor)
                .lineLimit(3)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            // Platform · source · comments · time metadata below headline
            let textParts = metadataTextParts
            if !textParts.isEmpty || item.commentCountDisplay != nil {
                HStack(spacing: 6) {
                    Text(textParts.joined(separator: " · "))
                        .font(.feedMeta)
                        .tracking(0.4)
                        .foregroundStyle(hasPlatform ? Color.platformLabel : Color.onSurfaceSecondary)
                        .lineLimit(1)
                        .truncationMode(.tail)

                    if let comments = item.commentCountDisplay {
                        HStack(spacing: 3) {
                            Text("·")
                                .font(.feedMeta)
                                .foregroundStyle(Color.onSurfaceSecondary)
                            Image(systemName: "bubble.left")
                                .font(.system(size: 9, weight: .medium))
                            Text(comments)
                                .font(.feedMeta)
                                .tracking(0.4)
                        }
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .fixedSize()
                    }
                }
                .lineLimit(1)
            }

            // Discussion snippet
            if let snippet = item.discussionSnippet {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "bubble.left.fill")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.onSurfaceSecondary.opacity(0.6))
                        .padding(.top, 3)
                    (
                        Text("\(snippet.author): ")
                            .font(.feedSnippet.weight(.semibold))
                            .foregroundStyle(Color.onSurface.opacity(0.7))
                        +
                        Text(snippet.text)
                            .font(.feedSnippet)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    )
                        .lineLimit(2)
                        .lineSpacing(2)
                }
                .padding(.top, 2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 16)
        .overlay(alignment: .bottom) {
            Divider()
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("short.row.\(item.id)")
    }
}

// MARK: - Day Delimiter

private struct DayDelimiter: View, Equatable {
    let item: ContentSummary
    let isFirst: Bool

    private static let monthDayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        formatter.timeZone = TimeZone.current
        return formatter
    }()

    private var dayLabel: String {
        guard let date = item.itemDate else { return "" }
        let calendar = Calendar.current

        if calendar.isDateInToday(date) {
            return "TODAY"
        } else if calendar.isDateInYesterday(date) {
            return "YESTERDAY"
        } else {
            return Self.monthDayFormatter.string(from: date).uppercased()
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            Text(dayLabel)
                .font(.system(size: 12, weight: .bold))
                .tracking(1.0)
                .foregroundStyle(Color.sectionDelimiter)
            Spacer()
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.top, isFirst ? 12 : 24)
        .padding(.bottom, 8)
        .overlay(alignment: .top) {
            if !isFirst {
                Rectangle()
                    .fill(Color.borderSubtle.opacity(0.4))
                    .frame(height: 6)
            }
        }
    }
}
