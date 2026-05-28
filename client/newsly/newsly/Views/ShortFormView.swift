//
//  ShortFormView.swift
//  newsly
//
//  Created by Assistant on 11/4/25.
//

import os.log
import SwiftUI
import UIKit

private let logger = Logger(subsystem: "com.newsly", category: "ShortFormView")

struct ShortFormView: View {
    @ObservedObject var viewModel: ShortNewsListViewModel
    let onSelect: (ContentDetailRoute) -> Void
    @StateObject private var processingCountService = ProcessingCountService.shared
    @StateObject private var narrationPlaybackService = NarrationPlaybackService.shared
    @State private var scrollReadTracker = ShortNewsScrollReadTracker()
    private let chatService = ChatService.shared

    @State private var showMarkAllConfirmation = false
    @State private var quickActionErrorMessage: String?
    @State private var activeQuickActionId: String?
    @State private var fastNewsAudioEpisode: AudioEpisode?
    @State private var isPreparingFastNewsAudio = false
    @State private var fastNewsAudioErrorMessage: String?

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
                    EditorialMastheadHeader(
                        title: "Fast Read"
                    )

                    shortNewsQuickActions(items: items)
                        .padding(.bottom, shouldShowFastNewsAudioControls ? 10 : 16)

                    BriefingStackCard(
                        items: items,
                        durationSeconds: fastNewsAudioEpisode?.durationSeconds,
                        isPreparing: isPreparingFastNewsAudio,
                        isPlaying: isPlayingFastNewsAudio,
                        onPlay: handleFastNewsAudioEpisode
                    )
                    .padding(.horizontal, Spacing.screenHorizontal)
                    .padding(.bottom, 22)

                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        // Day delimiter: show when this item starts a new day
                        if index == 0 || item.calendarDayKey != items[index - 1].calendarDayKey {
                            DayDelimiter(item: item, isFirst: index == 0)
                                .equatable()
                        }

                        ShortNewsRow(
                            item: item,
                            onDigDeeper: { selectedText in
                                FeedDigDeeperAction.start(
                                    selectedText: selectedText,
                                    item: item,
                                    visibleContentIds: items.prefix(15).map(\.id),
                                    surface: .shortNews
                                )
                            }
                        )
                            .equatable()
                            .contentShape(Rectangle())
                            .id(item.id)
                            .highPriorityGesture(
                                TapGesture().onEnded {
                                    let route = ContentDetailRoute(
                                        contentId: item.id,
                                        contentType: item.contentTypeEnum ?? .news,
                                        allContentIds: items.map(\.id)
                                    )
                                    onSelect(route)
                                }
                            )
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
                                .font(.terracottaBodyMedium.weight(.semibold))
                                .foregroundStyle(Color.onSurface)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 13)
                                .background(Color.surfaceSecondary.opacity(0.78))
                                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                                .overlay {
                                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                                        .stroke(Color.outlineVariant.opacity(0.42), lineWidth: 1)
                                }
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
            scrollReadTracker.updateTopVisibleItemId(visibleIds.first)
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

    private var isPlayingFastNewsAudio: Bool {
        guard let target = fastNewsAudioTarget else { return false }
        return narrationPlaybackService.isSpeaking
            && narrationPlaybackService.speakingTarget == target
    }

    private var fastNewsAudioTarget: NarrationTarget? {
        fastNewsAudioEpisode.map { .audioEpisode($0.id) }
    }

    private var isFastNewsAudioCurrent: Bool {
        guard let target = fastNewsAudioTarget else { return false }
        return narrationPlaybackService.speakingTarget == target
    }

    private var shouldShowFastNewsAudioControls: Bool {
        isPreparingFastNewsAudio || isFastNewsAudioCurrent
    }

    private func handleFastNewsAudioEpisode() {
        if isPlayingFastNewsAudio {
            narrationPlaybackService.pause()
            return
        }
        guard !isPreparingFastNewsAudio else { return }

        isPreparingFastNewsAudio = true
        fastNewsAudioErrorMessage = nil

        Task { @MainActor in
            let startedAt = Date()
            logger.info("[FastNewsAudio] flow started")
            defer { isPreparingFastNewsAudio = false }

            do {
                let episode: AudioEpisode
                if let existingEpisode = fastNewsAudioEpisode {
                    episode = existingEpisode
                    logger.info(
                        "[FastNewsAudio] reusing episode | episodeId=\(episode.id) status=\(episode.status, privacy: .public)"
                    )
                } else {
                    episode = try await AudioEpisodeService.shared.createFastNewsEpisode(
                        delivery: .inline
                    )
                    logger.info(
                        "[FastNewsAudio] episode created | episodeId=\(episode.id) status=\(episode.status, privacy: .public) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
                    )
                }
                fastNewsAudioEpisode = episode
                let target = NarrationTarget.audioEpisode(episode.id)
                try await narrationPlaybackService.playStreamingNarration(
                    for: target,
                    fetchStreamResource: {
                        try await AudioEpisodeService.shared.streamResource(for: episode)
                    }
                )
                logger.info(
                    "[FastNewsAudio] playback requested | episodeId=\(episode.id) elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000))"
                )
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                logger.error(
                    "[FastNewsAudio] flow failed | elapsedMs=\(Int(Date().timeIntervalSince(startedAt) * 1000)) error=\(error.localizedDescription, privacy: .public)"
                )
                fastNewsAudioErrorMessage = error.localizedDescription
            }
        }
    }

    private func markItemsAboveAsRead() {
        let items = viewModel.currentItems()
        let idsToMark = scrollReadTracker.idsToMarkAboveTop(in: items)
        guard !idsToMark.isEmpty else { return }

        logger.info("[ShortFormView] Items scrolled past top | ids=\(idsToMark, privacy: .public)")
        viewModel.itemsScrolledPastTop(ids: idsToMark)
    }

    @ViewBuilder
    private func shortNewsQuickActions(items: [ContentSummary]) -> some View {
        let quickActions = makeQuickActions(items: items)

        VStack(alignment: .leading, spacing: 10) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    Button {
                        handleFastNewsAudioEpisode()
                    } label: {
                        ShortNewsAudioActionChip(
                            isLoading: isPreparingFastNewsAudio,
                            isPlaying: isPlayingFastNewsAudio
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(isPreparingFastNewsAudio)
                    .accessibilityIdentifier("short.audio.fast_reads")

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

            if shouldShowFastNewsAudioControls {
                NarrationPlaybackControlRow(
                    playbackService: narrationPlaybackService,
                    target: fastNewsAudioTarget,
                    isPreparing: isPreparingFastNewsAudio,
                    onTogglePlayback: handleFastNewsAudioEpisode
                )
                .padding(.horizontal, Spacing.screenHorizontal)
            }

            if let fastNewsAudioErrorMessage {
                Text(fastNewsAudioErrorMessage)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.statusDestructive)
                    .padding(.horizontal, Spacing.screenHorizontal)
            }

            if let quickActionErrorMessage {
                Text(quickActionErrorMessage)
                    .font(.terracottaBodySmall)
                    .foregroundStyle(Color.statusDestructive)
                    .padding(.horizontal, Spacing.screenHorizontal)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func makeQuickActions(items: [ContentSummary]) -> [ShortNewsQuickAction] {
        let visibleItemIds = Array(items.prefix(15).map(\.id))

        return [
            ShortNewsQuickAction(
                id: "best_unread",
                title: "Best Unread",
                systemImage: "sparkles",
                prompt: InterestingUnreadNewsAssistantAction.prompt,
                screenContext: InterestingUnreadNewsAssistantAction.screenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast Read"
                )
            ),
            ShortNewsQuickAction(
                id: "summarize_top_15",
                title: "Summarize Top 15",
                systemImage: "text.alignleft",
                prompt: "Summarize the top 15 news items in my short news feed right now.",
                screenContext: AssistantScreenContext(
                    screenType: "short_news_feed",
                    screenTitle: "Fast Read",
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
                    screenTitle: "Fast Read",
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
                    screenTitle: "Fast Read",
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
                ChatNavigationCoordinator.shared.openAssistantTurn(response)
            } catch where isNetworkCancellation(error) {
                return
            } catch {
                quickActionErrorMessage = error.localizedDescription
            }
        }
    }

    @ViewBuilder
    private var shortFormEmptyState: some View {
        if processingCountService.newsProcessingCount > 0 || processingCountService.newsCrawlCount > 0 {
            ShortFormSetupEmptyState(
                processingCount: processingCountService.newsProcessingCount,
                crawlingSourceCount: processingCountService.newsCrawlCount
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .containerRelativeFrame(.vertical)
        } else {
            EmptyStateView(
                icon: "bolt.fill",
                title: "No Fast Reads Yet",
                subtitle: "Fresh items from your selected news sources will appear here once processed"
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .containerRelativeFrame(.vertical)
        }
    }
}

@MainActor
private final class ShortNewsScrollReadTracker {
    private var topVisibleItemId: Int?
    private var markedAsReadIds: Set<Int> = []

    func updateTopVisibleItemId(_ itemId: Int?) {
        topVisibleItemId = itemId
    }

    func idsToMarkAboveTop(in items: [ContentSummary]) -> [Int] {
        guard let topVisibleItemId,
              let topIndex = items.firstIndex(where: { $0.id == topVisibleItemId })
        else {
            return []
        }

        let idsToMark = items.prefix(topIndex).compactMap { item -> Int? in
            guard !item.isRead, !markedAsReadIds.contains(item.id) else { return nil }
            return item.id
        }

        markedAsReadIds.formUnion(idsToMark)
        return idsToMark
    }
}

private struct ShortFormSetupEmptyState: View {
    let processingCount: Int
    let crawlingSourceCount: Int

    private var title: String {
        if processingCount > 0 {
            return "Preparing \(processingCount) Fast \(processingCount == 1 ? "Read" : "Reads")"
        }
        return "Crawling \(crawlingSourceCount) \(crawlingSourceCount == 1 ? "Source" : "Sources")"
    }

    private var subtitle: String {
        if processingCount > 0 && crawlingSourceCount > 0 {
            return "We're checking your sources and summarizing new items as they arrive."
        }
        if processingCount > 0 {
            return "Summaries will appear here as soon as processing finishes."
        }
        return "We're checking your selected sources now. Fast Reads will appear as soon as the first item is ready."
    }

    var body: some View {
        VStack(spacing: 16) {
            ProgressView()
                .controlSize(.regular)

            VStack(spacing: 4) {
                Text(title)
                    .font(.listTitle.weight(.semibold))
                    .foregroundStyle(Color.onSurface)
                    .multilineTextAlignment(.center)

                Text(subtitle)
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 280)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }
}

// MARK: - Short News Row

private struct ShortNewsRow: View, Equatable {
    let item: ContentSummary
    var onDigDeeper: ((String) -> Void)?

    static func == (lhs: ShortNewsRow, rhs: ShortNewsRow) -> Bool {
        lhs.item == rhs.item
    }

    private var titleUIColor: UIColor {
        item.isRead ? .appOnSurfaceSecondary : .appOnSurface
    }

    private var titleUIFont: UIFont {
        .appTerracottaHeadlineCompact
    }

    private var metadataColor: Color {
        Color.platformLabel.opacity(0.9)
    }

    private var metadataParts: [String] {
        var parts: [String] = []
        if let source = FastReadPresentation.sourceLabel(for: item) {
            parts.append(source)
        }
        if let time = item.relativeTimeDisplay {
            parts.append(time.uppercased())
        }
        return parts
    }

    var body: some View {
        let metadata = metadataParts

        VStack(alignment: .leading, spacing: 7) {
            SelectableText(
                item.displayTitle,
                textColor: titleUIColor,
                font: titleUIFont,
                lineLimit: 3,
                lineBreakMode: .byTruncatingTail,
                onDigDeeper: onDigDeeper
            )
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)

            if !metadata.isEmpty || item.commentCountDisplay != nil {
                metadataRow(parts: metadata)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.vertical, 14)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(Color.borderSubtle.opacity(0.48))
                .frame(height: 1)
                .padding(.horizontal, Spacing.rowHorizontal)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("short.row.\(item.id)")
    }

    private func metadataRow(parts metadataParts: [String]) -> some View {
        HStack(spacing: 6) {
            if !metadataParts.isEmpty {
                Text(metadataParts.joined(separator: "  •  "))
                    .font(.terracottaCategoryPill)
                    .tracking(1.5)
                    .foregroundStyle(metadataColor)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }

            if let comments = item.commentCountDisplay {
                if !metadataParts.isEmpty {
                    Text("•")
                        .font(.terracottaCategoryPill)
                        .foregroundStyle(metadataColor)
                        .accessibilityHidden(true)
                }

                Image(systemName: "bubble.left")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(metadataColor)
                    .accessibilityHidden(true)

                Text(comments)
                    .font(.terracottaCategoryPill)
                    .tracking(1.1)
                    .foregroundStyle(metadataColor)
                    .monospacedDigit()
            }
        }
        .lineLimit(1)
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
        HStack(spacing: 10) {
            Text(dayLabel)
                .font(.terracottaCategoryPill)
                .tracking(1.9)
                .foregroundStyle(Color.sectionDelimiter)

            Rectangle()
                .fill(Color.brandTertiary.opacity(0.36))
                .frame(height: 1)
        }
        .padding(.horizontal, Spacing.rowHorizontal)
        .padding(.top, isFirst ? 12 : 20)
        .padding(.bottom, 7)
    }
}
