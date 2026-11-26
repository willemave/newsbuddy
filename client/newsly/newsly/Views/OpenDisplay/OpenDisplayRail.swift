//
//  OpenDisplayRail.swift
//  newsly
//

import SwiftUI

/// The open display's tight, icon-only navigation rail: Briefing tiers with
/// unread badges, Knowledge, and the Briefing player pinned at the
/// bottom. It sits at the leading edge, away from the fold. Names are spoken
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

            if let player {
                playerButton(player)
            }
        }
        .padding(.top, 12)
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
                            .overlay(Capsule().strokeBorder(Color.surfaceTertiary, lineWidth: 1.5))
                            .offset(x: 5, y: -4)
                            .accessibilityHidden(true)
                    }
                }
                // A full 44 pt target around the 36 pt tile.
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
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
        .accessibilityLabel(item.badge > 0 ? "\(item.title), \(item.badge) unread" : item.title)
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
        .accessibilityIdentifier(item.accessibilityIdentifier)
    }

    private func playerButton(_ player: PlayerState) -> some View {
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
        .accessibilityIdentifier("open.rail.player")
    }
}
