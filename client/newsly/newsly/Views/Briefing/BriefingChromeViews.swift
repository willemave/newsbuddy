import SwiftUI

/// Top-level pills: one aggregate "News" pill plus every fixed (podcasts /
/// articles) lens. Lives above the pager so it stays put while pages swipe.
struct BriefingTierStrip: View {
    @ObservedObject var viewModel: BriefingViewModel
    let onSelectNews: () -> Void
    let onSelectLens: (String) -> Void

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
                        accessibilityId: "briefing.lens.\(lens.key)"
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

/// During first-run onboarding, ready categories append directly beside the
/// synthetic Welcome page. The rail never shows placeholder categories.
struct BriefingFirstRunStrip: View {
    @ObservedObject var viewModel: BriefingViewModel
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
/// News categories revealed by the News pill, stacked into two packed rows
/// that scroll together; the pager swipes through exactly these, so the
/// selected pill follows the swipe.
struct BriefingCategoryStrip: View {
    @ObservedObject var viewModel: BriefingViewModel
    let onSelectLens: (String) -> Void
    let onRequestMarkAllRead: (APIBriefingLensSummary) -> Void

    /// Even indices on top, odd below, so neighbors in swipe order sit next
    /// to each other. A handful of categories stays on a single row.
    private var rows: [[APIBriefingLensSummary]] {
        let lenses = viewModel.newsLenses
        guard lenses.count >= 4 else { return [lenses] }
        var top: [APIBriefingLensSummary] = []
        var bottom: [APIBriefingLensSummary] = []
        for (index, lens) in lenses.enumerated() {
            if index.isMultiple(of: 2) {
                top.append(lens)
            } else {
                bottom.append(lens)
            }
        }
        return [top, bottom]
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                        HStack(spacing: 8) {
                            ForEach(row, id: \.key) { lens in
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
                    .onLongPressGesture(minimumDuration: 0.5, maximumDistance: 12) {
                        longPressFeedbackTrigger += 1
                        longPressAction()
                    }
                    .accessibilityHint("Long press to mark this category as read")
                    .accessibilityAction(named: "Mark All as Read") {
                        longPressFeedbackTrigger += 1
                        longPressAction()
                    }
            } else {
                pillButton
            }
        }
        .sensoryFeedback(.impact(weight: .medium), trigger: longPressFeedbackTrigger)
    }

    private var pillButton: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(title)
                    .font(.appCaption.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)

                if unreadCount > 0 {
                    Text("\(unreadCount)")
                        .font(.appCaption2.weight(.bold).monospacedDigit())
                        .foregroundStyle(isSelected ? Color.surfacePrimary : Color.brandPrimary)
                        .contentTransition(.numericText(countsDown: true))
                        .animation(.easeInOut(duration: 0.3), value: unreadCount)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(
                            Capsule()
                                .fill(isSelected ? Color.onSurface : Color.brandPrimary.opacity(0.12))
                        )
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
