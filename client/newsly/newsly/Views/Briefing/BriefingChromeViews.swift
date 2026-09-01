import SwiftUI

/// Top-level pills: one aggregate "News" pill plus every fixed (podcasts /
/// articles) lens. Lives above the pager so it stays put while pages swipe.
struct BriefingTierStrip: View {
    let viewModel: BriefingViewModel
    let onSelectNews: () -> Void
    let onSelectLens: (String) -> Void
    let onRequestMarkAllRead: (APIBriefingLensSummary) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if !viewModel.newsLenses.isEmpty {
                    BriefingStripPill(
                        title: "News",
                        unreadCount: viewModel.newsUnreadSourceCount,
                        isSelected: viewModel.isNewsTierSelected,
                        accessibilityId: "briefing.tier.news",
                        action: onSelectNews
                    )
                }

                ForEach(viewModel.fixedLenses, id: \.key) { lens in
                    BriefingStripPill(
                        title: lens.title,
                        unreadCount: lens.unreadSourceCount,
                        isSelected: lens.key == viewModel.selectedLensKey,
                        accessibilityId: "briefing.lens.\(lens.key)",
                        longPressAction: lens.unreadSourceCount > 0
                            ? { onRequestMarkAllRead(lens) }
                            : nil
                    ) {
                        onSelectLens(lens.key)
                    }
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 10)
        }
        .briefingTrailingScrollFade()
        .accessibilityIdentifier("briefing.lenses")
    }
}

/// During first-run onboarding, assigned categories append beside Welcome.
/// They become interactive as soon as their first segment is readable.
struct BriefingFirstRunStrip: View {
    let viewModel: BriefingViewModel
    let onSelectStartHere: () -> Void
    let onSelectLens: (String) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                BriefingStripPill(
                    title: "Welcome",
                    unreadCount: 0,
                    isSelected: viewModel.isStartHereSelected,
                    accessibilityId: "briefing.start_here.pill"
                ) {
                    onSelectStartHere()
                }

                ForEach(viewModel.orderedLenses, id: \.key) { lens in
                    BriefingStripPill(
                        title: lens.title,
                        unreadCount: lens.unreadSourceCount,
                        isSelected: false,
                        accessibilityId: "briefing.lens.\(lens.key)"
                    ) {
                        onSelectLens(lens.key)
                    }
                    .disabled(lens.segmentCount == 0)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
                }
            }
            .padding(.horizontal, Spacing.appHorizontalMargin)
            .padding(.vertical, 10)
            .animation(.easeOut(duration: 0.35), value: viewModel.orderedLenses.map(\.key))
        }
        .briefingTrailingScrollFade()
        .sensoryFeedback(
            .impact(weight: .light),
            trigger: !viewModel.orderedLenses.isEmpty
        ) { wasVisible, isVisible in
            !wasVisible && isVisible
        }
        .accessibilityIdentifier("briefing.first_run.lenses")
    }
}
/// News categories revealed by the News pill, on a single scrolling row; the
/// pager swipes through exactly these, so the selected pill follows the swipe.
///
/// This was two stacked rows, which put roughly 180pt of controls above the
/// first sentence of news. One row costs a little more horizontal scrolling and
/// buys back most of that height.
struct BriefingCategoryStrip: View {
    let viewModel: BriefingViewModel
    let onSelectLens: (String) -> Void
    let onRequestMarkAllRead: (APIBriefingLensSummary) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(viewModel.newsLenses, id: \.key) { lens in
                        BriefingStripPill(
                            title: lens.title,
                            unreadCount: lens.unreadSourceCount,
                            isSelected: lens.key == viewModel.selectedLensKey,
                            accessibilityId: "briefing.lens.\(lens.key)",
                            minHeight: 30,
                            longPressAction: lens.unreadSourceCount > 0
                                ? { onRequestMarkAllRead(lens) }
                                : nil
                        ) {
                            onSelectLens(lens.key)
                        }
                        .id(lens.key)
                    }
                }
                .padding(.horizontal, Spacing.appHorizontalMargin)
                .padding(.bottom, 10)
            }
            .onAppear {
                guard let selectedKey = viewModel.selectedLensKey else { return }
                proxy.scrollTo(selectedKey, anchor: .center)
            }
            .onChange(of: viewModel.selectedLensKey) { _, selectedKey in
                guard let selectedKey else { return }
                withAnimation(.easeInOut(duration: 0.22)) {
                    proxy.scrollTo(selectedKey, anchor: .center)
                }
            }
        }
        .briefingTrailingScrollFade()
        .accessibilityIdentifier("briefing.categories")
    }
}

private extension View {
    func briefingTrailingScrollFade() -> some View {
        overlay(alignment: .trailing) {
            LinearGradient(
                colors: [Color.surfacePrimary.opacity(0), Color.surfacePrimary],
                startPoint: .leading,
                endPoint: .trailing
            )
            .frame(width: 24)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
    }
}

private struct BriefingStripPill: View {
    let title: String
    let unreadCount: Int
    let isSelected: Bool
    let accessibilityId: String
    var minHeight: CGFloat = 36
    var longPressAction: (() -> Void)? = nil
    let action: () -> Void

    @State private var longPressFeedbackTrigger = 0

    var body: some View {
        Group {
            if let longPressAction {
                pillButton
                    .highPriorityGesture(
                        LongPressGesture(minimumDuration: 0.5, maximumDistance: 12)
                            .onEnded { _ in
                                performLongPressAction(longPressAction)
                            }
                    )
                    .accessibilityHint("Long press to mark this category as read")
                    .accessibilityAction(named: "Mark All as Read") {
                        performLongPressAction(longPressAction)
                    }
            } else {
                pillButton
            }
        }
        .sensoryFeedback(.impact(weight: .medium), trigger: longPressFeedbackTrigger)
    }

    private func performLongPressAction(_ action: () -> Void) {
        longPressFeedbackTrigger += 1
        action()
    }

    private var pillButton: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(title)
                    .font(.appCaption.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)

                // Bare digits rather than a badge: a capsule inside a capsule read as
                // two nested shapes for what is really one label.
                if unreadCount > 0 {
                    Text("\(unreadCount)")
                        .font(.appCaption2.weight(.bold).monospacedDigit())
                        .foregroundStyle(
                            isSelected ? Color.surfacePrimary.opacity(0.65) : Color.brandPrimary
                        )
                        .contentTransition(.numericText(countsDown: true))
                        .animation(.easeInOut(duration: 0.3), value: unreadCount)
                }
            }
            .foregroundStyle(isSelected ? Color.surfacePrimary : Color.onSurface)
            .frame(minHeight: minHeight)
            .padding(.horizontal, 12)
            .background(
                Capsule()
                    .fill(isSelected ? Color.onSurface : Color.surfaceSecondary)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title), \(unreadCount) unread sources")
        .accessibilityIdentifier(accessibilityId)
    }
}

/// Compact capsule that lives in the lens header; playback controls expand
/// below the header only while this lens is preparing or playing.
struct BriefingListenButton: View {
    let isPreparing: Bool
    let isPlaying: Bool
    let onToggle: () -> Void

    var body: some View {
        Button(action: onToggle) {
            HStack(spacing: 5) {
                if isPreparing {
                    ProgressView()
                        .controlSize(.mini)
                        .tint(Color.brandPrimary)
                } else {
                    Image(systemName: isPlaying ? "pause.fill" : "headphones")
                        .font(.appSymbol(size: 11, weight: .semibold))
                }

                Text(isPlaying ? "Pause" : "Listen")
                    .font(.appCaption.weight(.semibold))
            }
            .foregroundStyle(Color.brandPrimary)
            .padding(.horizontal, 12)
            .frame(height: 30)
            .background(Capsule().fill(Color.brandPrimary.opacity(0.12)))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .disabled(isPreparing)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityIdentifier("briefing.narration.play")
    }

    private var accessibilityLabel: String {
        if isPreparing {
            return "Preparing briefing audio"
        }
        return isPlaying ? "Pause briefing audio" : "Play briefing audio"
    }
}

/// Chapter navigation sits above the shared playback row: previous/next are
/// immediate, while the center label opens the full chapter list.
struct BriefingNarrationChapterControls: View {
    let narration: BriefingNarration
    let selectedIndex: Int
    let playbackService: NarrationPlaybackService
    let target: NarrationTarget?
    let isPreparing: Bool
    let onPrevious: () -> Void
    let onShowChapters: () -> Void
    let onNext: () -> Void
    let onTogglePlayback: () -> Void

    private var boundedIndex: Int {
        guard !narration.chapters.isEmpty else { return 0 }
        return min(max(selectedIndex, 0), narration.chapters.count - 1)
    }

    private var selectedChapter: AudioEpisode? {
        guard narration.chapters.indices.contains(boundedIndex) else { return nil }
        return narration.chapters[boundedIndex]
    }

    var body: some View {
        VStack(spacing: 3) {
            HStack(spacing: 6) {
                chapterNavigationButton(
                    systemName: "chevron.left",
                    accessibilityLabel: "Previous chapter",
                    isDisabled: boundedIndex == 0,
                    action: onPrevious
                )

                Button(action: onShowChapters) {
                    HStack(spacing: 5) {
                        Text(chapterLabel)
                            .font(.appCaption.weight(.semibold))
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                        Image(systemName: "chevron.down")
                            .font(.appSymbol(size: 9, weight: .bold))
                    }
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .frame(maxWidth: .infinity, minHeight: 36)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Choose chapter. \(chapterLabel)")
                .accessibilityIdentifier("briefing.narration.chapters")

                chapterNavigationButton(
                    systemName: "chevron.right",
                    accessibilityLabel: "Next chapter",
                    isDisabled: boundedIndex >= narration.chapters.count - 1,
                    action: onNext
                )
            }
            .padding(.horizontal, 6)

            NarrationPlaybackControlRow(
                playbackService: playbackService,
                target: target,
                isPreparing: isPreparing,
                onTogglePlayback: onTogglePlayback
            )
        }
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.5), lineWidth: 1)
        }
    }

    private var chapterLabel: String {
        let count = narration.chapters.count
        guard count > 0 else { return "Chapters" }
        let duration = selectedChapter?.durationSeconds ?? 0
        let roundedMinutes = max(1, Int((Double(duration) / 60).rounded()))
        let durationLabel = duration > 0 ? " · ~\(roundedMinutes) min" : ""
        let titleLabel = selectedChapter.map { " · \($0.title)" } ?? ""
        return "Chapter \(boundedIndex + 1) of \(count)\(titleLabel)\(durationLabel)"
    }

    private func chapterNavigationButton(
        systemName: String,
        accessibilityLabel: String,
        isDisabled: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.appSymbol(size: 12, weight: .semibold))
                .foregroundStyle(Color.brandPrimary)
                .frame(width: 44, height: 36)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isDisabled || isPreparing)
        .opacity(isDisabled ? 0.35 : 1)
        .accessibilityLabel(accessibilityLabel)
    }
}
