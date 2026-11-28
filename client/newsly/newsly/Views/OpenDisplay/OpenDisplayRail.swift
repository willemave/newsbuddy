//
//  OpenDisplayRail.swift
//  newsly
//

import SwiftUI

/// The open display's tight, icon-only navigation rail: Briefing tiers with
/// unread badges and Knowledge at the top; Search, Settings, and the
/// Briefing player pinned at the bottom. It sits at the leading edge, away from the fold. Names are spoken
/// by VoiceOver and shown on long press, never as visible labels.
struct OpenDisplayRail: View {
    struct Item: Identifiable {
        let destination: OpenRailDestination
        let title: String
        let systemImage: String
        let badge: Int
        let accessibilityIdentifier: String

        var id: OpenRailDestination { destination }
    }

    let briefingItems: [Item]
    let knowledgeItem: Item
    let selection: OpenRailDestination?
    let player: PlayerState?
    let onSelect: (OpenRailDestination) -> Void
    let onSearch: () -> Void
    let onOpenSettings: () -> Void
    let settingsNeedsAttention: Bool

    struct PlayerState {
        let isPreparing: Bool
        let isPlaying: Bool
        let onToggle: () -> Void
    }

    @State private var peekedItem: OpenRailDestination?
    @State private var peekGeneration = 0

    var body: some View {
        VStack(spacing: 4) {
            ForEach(briefingItems) { railButton($0) }

            Rectangle()
                .fill(Color.borderStrong.opacity(0.6))
                .frame(width: 20, height: 1)
                .padding(.vertical, 6)
                .accessibilityHidden(true)

            railButton(knowledgeItem)

            Spacer(minLength: 12)

            utilityButton(
                systemImage: "magnifyingglass",
                label: "Search saved knowledge",
                identifier: "open.rail.search",
                action: onSearch
            )
            utilityButton(
                systemImage: "gearshape",
                label: "Settings",
                identifier: "open.rail.settings",
                showsAttention: settingsNeedsAttention,
                action: onOpenSettings
            )
            .padding(.bottom, 8)

            if let player {
                RailPlayerButton(player: player)
                    .accessibilityIdentifier("open.rail.player")
            }
        }
        // The display's rounded corner curves through the top of the rail;
        // the first item starts one item lower, clear of it.
        .padding(.top, 56)
        .padding(.bottom, 14)
        .frame(width: OpenDisplayMetrics.railWidth)
        .frame(maxHeight: .infinity)
        .background(Color.surfaceTertiary.ignoresSafeArea())
        .zIndex(2)
        .task(id: peekGeneration) {
            guard peekedItem != nil else { return }
            try? await Task.sleep(for: .seconds(1.4))
            guard !Task.isCancelled else { return }
            withAnimation(AppMotion.subtle) { peekedItem = nil }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Navigation")
        .accessibilityIdentifier("open.rail")
    }

    private func railButton(_ item: Item) -> some View {
        let isSelected = item.destination == selection
        return Button {
            onSelect(item.destination)
        } label: {
            RailIconTile(item: item, isSelected: isSelected, backing: .surfaceTertiary)
        }
        .buttonStyle(.plain)
        .simultaneousGesture(
            LongPressGesture(minimumDuration: 0.4).onEnded { _ in
                withAnimation(AppMotion.subtle) { peekedItem = item.destination }
                peekGeneration += 1
            }
        )
        .overlay(alignment: .leading) {
            if peekedItem == item.destination {
                Text(item.title)
                    .font(.appCaption.weight(.semibold))
                    .foregroundStyle(Color.surfacePrimary)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(Capsule().fill(Color.onSurface))
                    .fixedSize()
                    .offset(x: 44)
                    .transition(.opacity)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
        .railItemAccessibility(item, isSelected: isSelected)
    }

    /// Search and Settings: actions, not destinations, so they never show
    /// as selected.
    private func utilityButton(
        systemImage: String,
        label: String,
        identifier: String,
        showsAttention: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.appSymbol(size: 17))
                .foregroundStyle(Color.onSurfaceSecondary)
                .overlay(alignment: .topTrailing) {
                    if showsAttention {
                        Circle()
                            .fill(Color.statusDestructive)
                            .frame(width: 8, height: 8)
                            .overlay(Circle().stroke(Color.surfaceTertiary, lineWidth: 1.5))
                            .offset(x: 3, y: -2)
                    }
                }
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityValue(showsAttention ? "Needs attention" : "")
        .accessibilityIdentifier(identifier)
    }
}

/// A rail destination's icon tile with its unread badge, inside a 44 pt
/// target. Shared by the open-display rail and the closed Duo's status
/// column. `backing` is the surface behind it, which rims the badge.
struct RailIconTile: View {
    let item: OpenDisplayRail.Item
    let isSelected: Bool
    let backing: Color

    var body: some View {
        Image(systemName: item.systemImage)
            .font(.appSymbol(size: 17, weight: isSelected ? .semibold : .regular))
            .foregroundStyle(isSelected ? Color.onSurface : Color.onSurfaceSecondary)
            .frame(width: 36, height: 36)
            .background {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(isSelected ? Color.surfaceContainer : Color.clear)
            }
            .overlay(alignment: .topTrailing) {
                if item.badge > 0 {
                    Text(item.badge > 99 ? "99+" : "\(item.badge)")
                        .font(.appSans(size: 8, weight: .bold))
                        .foregroundStyle(Color.surfacePrimary)
                        .padding(.horizontal, 3.5)
                        .frame(minWidth: 14, minHeight: 14)
                        .background(Capsule().fill(Color.appChromeAccent))
                        .overlay(Capsule().strokeBorder(backing, lineWidth: 1.5))
                        .offset(x: 5, y: -4)
                        .accessibilityHidden(true)
                }
            }
            // A full 44 pt target around the 36 pt tile.
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }
}

/// The Briefing play button on the rail and in the closed Duo's status column.
struct RailPlayerButton: View {
    let player: OpenDisplayRail.PlayerState

    var body: some View {
        Button(action: player.onToggle) {
            ZStack {
                Circle().fill(Color.appChromeAccent)
                if player.isPreparing {
                    ProgressView()
                        .controlSize(.small)
                        .tint(Color.surfacePrimary)
                } else {
                    Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                        .font(.appSymbol(size: 13, weight: .semibold))
                        .foregroundStyle(Color.surfacePrimary)
                        .offset(x: player.isPlaying ? 0 : 1)
                }
            }
            .frame(width: 34, height: 34)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            player.isPreparing ? "Preparing briefing audio"
                : (player.isPlaying ? "Pause briefing audio" : "Play briefing audio")
        )
    }
}

extension View {
    func railItemAccessibility(_ item: OpenDisplayRail.Item, isSelected: Bool) -> some View {
        accessibilityLabel(item.badge > 0 ? "\(item.title), \(item.badge) unread" : item.title)
            .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
            .accessibilityIdentifier(item.accessibilityIdentifier)
    }
}

extension BriefingViewModel {
    /// Briefing's tiers as rail destinations, with unread badges.
    var railItems: [OpenDisplayRail.Item] {
        var items: [OpenDisplayRail.Item] = []
        if firstRun != nil {
            items.append(
                OpenDisplayRail.Item(
                    destination: .startHere,
                    title: "Welcome",
                    systemImage: "sparkles",
                    badge: 0,
                    accessibilityIdentifier: "open.rail.start_here"
                )
            )
        }
        items.append(
            OpenDisplayRail.Item(
                destination: .news,
                title: "News",
                systemImage: "newspaper",
                badge: newsUnreadSourceCount,
                accessibilityIdentifier: "open.rail.news"
            )
        )
        for lens in fixedLenses {
            items.append(
                OpenDisplayRail.Item(
                    destination: .lens(lens.key),
                    title: lens.title,
                    systemImage: lens.tier == .audio ? "waveform" : "text.book.closed",
                    badge: lens.unreadSourceCount,
                    accessibilityIdentifier: "open.rail.lens.\(lens.key)"
                )
            )
        }
        return items
    }

    /// The rail destination for the current Briefing selection.
    var railSelection: OpenRailDestination? {
        OpenRailDestination.current(
            selectedTab: .briefing,
            isStartHereSelected: isStartHereSelected,
            isNewsTierSelected: isNewsTierSelected,
            selectedLensKey: selectedLensKey
        )
    }

    /// Play state for the selected lens; nil before a lens is picked.
    var railPlayerState: OpenDisplayRail.PlayerState? {
        guard let lensKey = selectedLensKey else { return nil }
        let narration = narrationController
        return OpenDisplayRail.PlayerState(
            isPreparing: narration.session(for: lensKey).isPreparing,
            isPlaying: narration.isPlaying(lensKey: lensKey),
            onToggle: { Task { await narration.togglePlayback(for: lensKey) } }
        )
    }
}
