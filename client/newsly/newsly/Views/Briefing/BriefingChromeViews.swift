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
                        .foregroundStyle(isSelected ? Color.surfacePrimary : Color.brandPrimary)
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

/// Icon-only playback control in the lens header; playback controls expand
/// below the header only while this lens is preparing or playing.
struct BriefingListenButton: View {
    let isPreparing: Bool
    let isPlaying: Bool
    let onToggle: () -> Void

    var body: some View {
        Button(action: onToggle) {
            Group {
                if isPreparing {
                    ProgressView()
                        .controlSize(.small)
                        .tint(Color.brandPrimary)
                } else {
                    Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                        .font(.appSymbol(size: 22, weight: .semibold))
                        .offset(x: isPlaying ? 0 : 1)
                }
            }
            .foregroundStyle(Color.brandPrimary)
            .frame(width: 52, height: 52)
            .background(Circle().fill(Color.brandPrimary.opacity(0.14)))
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
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

/// The now-playing card beneath the pinned strips. It has two shapes: a
/// full card while the reader is at the top of the lens, and a one-line bar
/// once the masthead has collapsed so the page belongs to the text again.
/// Both shapes stay laid out so the swap is a crossfade, not a relayout.
struct BriefingNowPlayingPanel: View {
    let lensTitle: String
    let narration: BriefingNarration?
    let selectedIndex: Int
    let snapshot: NarrationPlaybackSnapshot
    let isMinimized: Bool
    let onTogglePlayback: () -> Void
    let onPrevious: () -> Void
    let onNext: () -> Void
    let onShowChapters: () -> Void
    let onSeek: (Double) -> Void
    let onSetPlaybackRate: (Float) -> Void
    let onExpand: () -> Void
    let onDismiss: () -> Void
    /// Natural heights of both shapes, owned by the host so it can subtract
    /// the difference from the chrome measurement in the same render pass
    /// the shape changes. The height swap is deliberately not animated: an
    /// animated height with an instant compensation would make the page
    /// inset dip, which the scroll probe reads as a scroll.
    @Binding var fullHeight: CGFloat
    @Binding var minimizedHeight: CGFloat

    private var chapterCount: Int { narration?.chapters.count ?? 0 }

    private var boundedIndex: Int {
        guard chapterCount > 0 else { return 0 }
        return min(max(selectedIndex, 0), chapterCount - 1)
    }

    private var selectedChapter: AudioEpisode? {
        guard let narration, narration.chapters.indices.contains(boundedIndex) else { return nil }
        return narration.chapters[boundedIndex]
    }

    private var hasPrevious: Bool { boundedIndex > 0 }
    private var hasNext: Bool { boundedIndex < chapterCount - 1 }

    private var visibleHeight: CGFloat? {
        let height = isMinimized ? minimizedHeight : fullHeight
        return height > 0 ? height : nil
    }

    var body: some View {
        ZStack(alignment: .top) {
            fullCard
                .fixedSize(horizontal: false, vertical: true)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { _, height in
                    guard height > 0, abs(height - fullHeight) > 0.5 else { return }
                    fullHeight = height
                }
                .opacity(isMinimized ? 0 : 1)
                .animation(.easeInOut(duration: 0.18), value: isMinimized)
                .allowsHitTesting(!isMinimized)
                .accessibilityHidden(isMinimized)

            minimizedBar
                .fixedSize(horizontal: false, vertical: true)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { _, height in
                    guard height > 0, abs(height - minimizedHeight) > 0.5 else { return }
                    minimizedHeight = height
                }
                .opacity(isMinimized ? 1 : 0)
                .animation(.easeInOut(duration: 0.18), value: isMinimized)
                .allowsHitTesting(isMinimized)
                .accessibilityHidden(!isMinimized)
        }
        .frame(height: visibleHeight, alignment: .top)
        .clipped()
        .transaction { $0.animation = nil }
    }

    // MARK: Full card

    private var fullCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 12) {
                NarrationPlayButton(
                    snapshot: snapshot,
                    diameter: 44,
                    subject: "briefing audio",
                    action: onTogglePlayback
                )
                .accessibilityIdentifier("briefing.narration.play")

                chapterHeading

                HStack(spacing: 0) {
                    NarrationSpeedMenu(playbackRate: snapshot.playbackRate, onSelect: onSetPlaybackRate)
                        .accessibilityIdentifier("briefing.narration.speed")
                    dismissButton
                }
            }
            .padding(.trailing, -10)

            HStack(alignment: .top, spacing: 2) {
                chapterStepButton(
                    systemName: "backward.end.fill",
                    accessibilityLabel: "Previous chapter",
                    isEnabled: hasPrevious,
                    action: onPrevious
                )
                .accessibilityIdentifier("briefing.narration.previous")

                NarrationScrubber(snapshot: snapshot, onSeek: onSeek)
                    .padding(.top, 4)

                chapterStepButton(
                    systemName: "forward.end.fill",
                    accessibilityLabel: "Next chapter",
                    isEnabled: hasNext,
                    action: onNext
                )
                .accessibilityIdentifier("briefing.narration.next")
            }
            .padding(.horizontal, -8)
        }
        .padding(.horizontal, 14)
        .padding(.top, 14)
        .padding(.bottom, 8)
        .background(
            RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                .fill(Color.surfaceSecondary)
        )
        .overlay(
            RoundedRectangle(cornerRadius: CornerRadius.control, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.35), lineWidth: 0.5)
        )
    }

    /// Kicker plus chapter title; the whole block opens the chapter list.
    private var chapterHeading: some View {
        Button(action: onShowChapters) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 4) {
                    Text(kickerText)
                        .kicker()
                        .lineLimit(1)
                    if chapterCount > 0 {
                        Image(systemName: "chevron.down")
                            .font(.appSymbol(size: 8, weight: .bold))
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }

                Text(headingTitle)
                    .font(.appHeadline)
                    .foregroundStyle(Color.onSurface)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(chapterCount == 0)
        .accessibilityLabel("Choose chapter. \(kickerText). \(headingTitle)")
        .accessibilityIdentifier("briefing.narration.chapters")
    }

    private var kickerText: String {
        guard chapterCount > 0 else { return lensTitle.uppercased() }
        var parts = ["CHAPTER \(boundedIndex + 1) OF \(chapterCount)"]
        if let duration = selectedChapter?.durationSeconds, duration > 0 {
            parts.append("\(max(1, Int((Double(duration) / 60).rounded()))) MIN")
        }
        return parts.joined(separator: " · ")
    }

    private var headingTitle: String {
        if let selectedChapter {
            return selectedChapter.title
        }
        return snapshot.isPreparing ? "Preparing your audio…" : lensTitle
    }

    private func chapterStepButton(
        systemName: String,
        accessibilityLabel: String,
        isEnabled: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.appSymbol(size: 15, weight: .semibold))
                .foregroundStyle(Color.onSurface)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!isEnabled || snapshot.isPreparing)
        .opacity(isEnabled ? 1 : 0.3)
        .accessibilityLabel(accessibilityLabel)
    }

    /// Stops audio and clears the player. Quiet on purpose: the accent
    /// belongs to Play, and this should read as "put it away", not "cancel".
    private var dismissButton: some View {
        Button(action: onDismiss) {
            Image(systemName: "xmark")
                .font(.appSymbol(size: 12, weight: .bold))
                .foregroundStyle(Color.onSurfaceSecondary)
                .frame(width: 36, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Stop audio and close player")
        .accessibilityIdentifier("briefing.narration.dismiss")
    }

    // MARK: Minimized bar

    private var minimizedBar: some View {
        HStack(spacing: 10) {
            NarrationPlayButton(
                snapshot: snapshot,
                diameter: 30,
                subject: "briefing audio",
                action: onTogglePlayback
            )
            .accessibilityIdentifier("briefing.narration.play")

            Button(action: onExpand) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(headingTitle)
                        .font(.appCaption.weight(.semibold))
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(1)
                    Text(minimizedDetail)
                        .font(.appCaption2.monospacedDigit())
                        .foregroundStyle(Color.onSurfaceSecondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Show audio controls. \(headingTitle). \(minimizedDetail)")
            .accessibilityIdentifier("briefing.narration.expand")

            chapterStepButton(
                systemName: "forward.end.fill",
                accessibilityLabel: "Next chapter",
                isEnabled: hasNext,
                action: onNext
            )
            .accessibilityIdentifier("briefing.narration.next")
            .padding(.trailing, -10)

            dismissButton
        }
        .padding(.leading, 8)
        .padding(.trailing, 2)
        .padding(.top, 4)
        .padding(.bottom, 7)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color.surfaceSecondary)
        )
        .overlay(alignment: .bottom) {
            // Hairline progress along the bottom edge stands in for the scrubber.
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.outlineVariant.opacity(0.6))
                    Capsule()
                        .fill(Color.brandPrimary)
                        .frame(width: max(geometry.size.width * snapshot.progress, 0))
                }
            }
            .frame(height: 2)
            .padding(.horizontal, 14)
            .padding(.bottom, 3)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
        }
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Color.outlineVariant.opacity(0.35), lineWidth: 0.5)
        )
    }

    private var minimizedDetail: String {
        var parts: [String] = []
        if chapterCount > 0 {
            parts.append("Chapter \(boundedIndex + 1) of \(chapterCount)")
        } else {
            parts.append(lensTitle)
        }
        if snapshot.canSeek {
            parts.append("\(narrationTimeLabel(snapshot.remainingTime)) left")
        } else if snapshot.isPreparing {
            parts.append("Preparing")
        }
        return parts.joined(separator: " · ")
    }
}
