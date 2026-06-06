//
//  LongFormCardStackView.swift
//  newsly
//
//  Swipeable card stack view for articles and podcasts.
//

import SwiftUI
import UIKit
import os.log

private let cardStackLogger = Logger(subsystem: "com.newsly", category: "LongFormCardStackView")

struct LongFormCardStackView: View {
    @ObservedObject var viewModel: LongContentListViewModel
    let onSelect: (ContentDetailRoute) -> Void

    @StateObject private var keyPointsLoader = CardStackKeyPointsLoader()

    @State private var currentIndex: Int = 0
    @State private var dragOffset: CGFloat = 0

    private let swipeThreshold: CGFloat = 100
    private let velocityThreshold: CGFloat = 500
    private let cardHorizontalPadding: CGFloat = 24

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                if viewModel.state == .initialLoading && items.isEmpty {
                    LoadingView()
                } else if case .error(let error) = viewModel.state, items.isEmpty {
                    ErrorView(message: error.localizedDescription) {
                        viewModel.refreshTrigger.send(())
                    }
                } else if items.isEmpty {
                    emptyStateView
                } else {
                    cardStackContent(geometry: geometry)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear {
            viewModel.setReadFilter(.unread)
            viewModel.refreshTrigger.send(())
        }
        .onChange(of: items.count) { oldCount, newCount in
            if newCount > 0 && currentIndex >= newCount {
                currentIndex = max(0, newCount - 1)
            }
            if oldCount == 0 && newCount > 0 {
                currentIndex = 0
            }
            if oldCount == 0 && newCount > 0 {
                cardStackLogger.info("[LongFormCardStackView] items loaded count=\(newCount)")
                Task {
                    await prefetchImages(reason: "items_loaded")
                    await prefetchKeyPoints(reason: "items_loaded")
                }
            }
        }
        .task(id: currentIndex) {
            await prefetchImages(reason: "index_change")
            await prefetchKeyPoints(reason: "index_change")
        }
    }

    private var items: [ContentSummary] {
        viewModel.currentItems()
    }

    private var emptyStateView: some View {
        EmptyStateView(
            icon: "doc.richtext",
            title: "No Long-Form Content",
            subtitle: "Articles and podcasts will appear here once processed"
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private func cardStackContent(geometry: GeometryProxy) -> some View {
        let cardWidth = geometry.size.width - (cardHorizontalPadding * 2)
        let cardHeight = geometry.size.height - 40

        VStack(spacing: 0) {
            Spacer().frame(height: 20)

            ZStack {
                ForEach(visibleCardIndices, id: \.self) { index in
                    let relativeOffset = index - currentIndex
                    let content = items[index]

                    ArticleCardView(
                        content: content,
                        keyPoints: keyPointsLoader.keyPoints(for: content.id),
                        hook: keyPointsLoader.hook(for: content.id),
                        topics: keyPointsLoader.topics(for: content.id),
                        isLoadingKeyPoints: keyPointsLoader.isLoading(content.id),
                        onSaveToKnowledge: { Task { await viewModel.toggleKnowledgeSave(content.id) } },
                        onMarkRead: { viewModel.markAsRead(content.id) },
                        onTap: { navigateToDetail(content) },
                        onDownloadMore: { count in
                            Task { await viewModel.downloadMoreFromSeries(contentId: content.id, count: count) }
                        },
                        scale: scaleForOffset(relativeOffset),
                        yOffset: yOffsetForOffset(relativeOffset),
                        cardOpacity: opacityForOffset(relativeOffset)
                    )
                    .frame(width: cardWidth, height: cardHeight)
                    .offset(x: xOffsetForCard(at: index, width: cardWidth))
                    .zIndex(Double(100 - abs(relativeOffset)))
                    .animation(
                        .interactiveSpring(response: 0.35, dampingFraction: 0.85),
                        value: currentIndex
                    )
                    .animation(
                        .interactiveSpring(response: 0.35, dampingFraction: 0.85),
                        value: dragOffset
                    )
                }
            }
            .frame(width: cardWidth, height: cardHeight)
            .contentShape(Rectangle())
            .highPriorityGesture(dragGesture)

            Spacer().frame(height: 20)

            paginationIndicator
                .padding(.bottom, 8)
        }
    }

    private var visibleCardIndices: [Int] {
        guard !items.isEmpty else { return [] }
        // Include previous card (for swipe-back), current, and 2 shadow cards
        // Render in reverse order so current card is on top
        let start = max(0, currentIndex - 1)
        let end = min(items.count - 1, currentIndex + 2)
        return Array((start...end).reversed())
    }

    private func scaleForOffset(_ offset: Int) -> CGFloat {
        if offset <= 0 { return 1.0 }
        // Shadow cards get progressively smaller
        return max(0.92, 1.0 - CGFloat(offset) * 0.03)
    }

    private func yOffsetForOffset(_ offset: Int) -> CGFloat {
        if offset <= 0 { return 0 }
        // Shadow cards shift down slightly
        return CGFloat(offset) * 6
    }

    private func opacityForOffset(_ offset: Int) -> Double {
        if offset <= 0 { return 1.0 }
        // Shadow cards are more visible to hint at swiping
        return max(0.6, 1.0 - Double(offset) * 0.2)
    }

    private func xOffsetForCard(at index: Int, width: CGFloat) -> CGFloat {
        let relativeOffset = index - currentIndex

        if relativeOffset < 0 {
            // Previous cards are off-screen to the left
            return CGFloat(relativeOffset) * (width + 40)
        }

        if relativeOffset == 0 {
            // Current card moves with drag
            return dragOffset
        }

        // Shadow cards peek from the right edge
        // Each card peeks out ~18pt more than the one in front
        let peekAmount: CGFloat = 18
        let baseOffset = CGFloat(relativeOffset) * peekAmount

        // Parallax: shadow cards shift left slightly as user drags left
        // This creates anticipation that the next card will appear
        let parallaxFactor: CGFloat = 0.15
        let parallaxOffset = min(0, dragOffset * parallaxFactor * CGFloat(relativeOffset))

        return baseOffset + parallaxOffset
    }

    private var dragGesture: some Gesture {
        DragGesture(minimumDistance: 30)
            .onChanged { value in
                if abs(value.translation.width) > abs(value.translation.height) {
                    dragOffset = value.translation.width
                }
            }
            .onEnded { value in
                handleDragEnd(
                    translation: value.translation.width,
                    predictedEnd: value.predictedEndTranslation.width
                )
            }
    }

    private func handleDragEnd(translation: CGFloat, predictedEnd: CGFloat) {
        let velocity = predictedEnd - translation
        var targetIndex = currentIndex

        if translation < -swipeThreshold || velocity < -velocityThreshold {
            if currentIndex < items.count - 1 {
                targetIndex = currentIndex + 1
            }
        } else if translation > swipeThreshold || velocity > velocityThreshold {
            if currentIndex > 0 {
                targetIndex = currentIndex - 1
            }
        }

        withAnimation(.interactiveSpring(response: 0.35, dampingFraction: 0.85)) {
            currentIndex = targetIndex
            dragOffset = 0
        }

        if targetIndex != currentIndex {
            triggerHaptic()
        }

        if currentIndex >= items.count - 3 {
            viewModel.loadMoreTrigger.send(())
        }
    }

    private var paginationIndicator: some View {
        HStack(spacing: 6) {
            // Show dots for small counts, otherwise show numeric
            if items.count <= 7 {
                ForEach(0..<items.count, id: \.self) { index in
                    Circle()
                        .fill(index == currentIndex ? Color.onSurface : Color.onSurfaceTertiary)
                        .frame(width: index == currentIndex ? 8 : 6, height: index == currentIndex ? 8 : 6)
                        .animation(.easeInOut(duration: 0.2), value: currentIndex)
                }
            } else {
                // For larger counts, show compact dot cluster with number
                HStack(spacing: 4) {
                    ForEach(0..<min(5, items.count), id: \.self) { i in
                        let dotIndex = paginationDotIndex(at: i)
                        Circle()
                            .fill(dotIndex == currentIndex ? Color.onSurface : Color.onSurfaceTertiary)
                            .frame(width: dotIndex == currentIndex ? 8 : 6, height: dotIndex == currentIndex ? 8 : 6)
                            .animation(.easeInOut(duration: 0.2), value: currentIndex)
                    }
                }

                Text("\(currentIndex + 1)/\(items.count)")
                    .font(.caption2)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .monospacedDigit()
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    /// Calculate which index a dot at position i should represent
    private func paginationDotIndex(at position: Int) -> Int {
        let total = items.count
        let visibleDots = 5

        if total <= visibleDots {
            return position
        }

        // Keep current index centered when possible
        let halfVisible = visibleDots / 2
        let start: Int

        if currentIndex <= halfVisible {
            start = 0
        } else if currentIndex >= total - halfVisible - 1 {
            start = total - visibleDots
        } else {
            start = currentIndex - halfVisible
        }

        return start + position
    }

    private func navigateToDetail(_ content: ContentSummary) {
        ContentImagePrefetcher.prefetch(content)
        let route = ContentDetailRoute(
            summary: content,
            allContentIds: items.map(\.id),
            navigationSurface: .longForm
        )
        onSelect(route)
    }

    private func prefetchImages(reason: String) async {
        let end = min(items.count, currentIndex + 3)
        guard currentIndex < end else { return }

        let prefetchItems = Array(items[currentIndex..<end])
        cardStackLogger.info(
            "[LongFormCardStackView] image prefetch (\(reason)) index=\(currentIndex) count=\(prefetchItems.count)"
        )
        let urls = prefetchItems.flatMap { ContentImagePrefetcher.urls(for: $0) }
        await ImageCacheService.shared.prefetch(urls: urls)
    }

    private func prefetchKeyPoints(reason: String) async {
        let contentIds = items.map(\.id)
        cardStackLogger.info(
            "[LongFormCardStackView] prefetch (\(reason)) index=\(currentIndex) ids=\(contentIds.count)"
        )
        await keyPointsLoader.prefetch(contentIds: contentIds, aroundIndex: currentIndex)
    }

    private func triggerHaptic() {
        let generator = UIImpactFeedbackGenerator(style: .light)
        generator.impactOccurred()
    }
}
