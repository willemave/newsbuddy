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

    /// Track which items have already been marked as read to avoid duplicates
    @State private var markedAsReadIds: Set<Int> = []
    @State private var showMarkAllConfirmation = false
    @State private var topVisibleItemId: Int?

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                if case .error(let error) = viewModel.state, viewModel.currentItems().isEmpty {
                    ErrorView(message: error.localizedDescription) {
                        viewModel.refreshTrigger.send(())
                    }
                    .padding(.top, 48)
                } else if viewModel.state == .initialLoading, viewModel.currentItems().isEmpty {
                    ProgressView("Loading")
                        .padding(.top, 48)
                } else if viewModel.currentItems().isEmpty {
                    shortFormEmptyState
                } else {
                    let items = viewModel.currentItems()

                    Text("Fast Read")
                        .font(.terracottaDisplayLarge)
                        .foregroundStyle(Color.onSurface)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, Spacing.screenHorizontal)
                        .padding(.top, 16)
                        .padding(.bottom, 24)

                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        // Day delimiter: show when this item starts a new day
                        if index == 0 || item.calendarDayKey != items[index - 1].calendarDayKey {
                            DayDelimiter(item: item, isFirst: index == 0)
                        }

                        ShortNewsRow(item: item)
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

                    if viewModel.currentItems().contains(where: { !$0.isRead }) {
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
        .scrollPosition(id: $topVisibleItemId, anchor: .top)
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

// MARK: - Short News Row

private struct ShortNewsRow: View {
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

private struct DayDelimiter: View {
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
