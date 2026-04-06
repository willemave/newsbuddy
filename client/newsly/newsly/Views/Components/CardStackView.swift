//
//  CardStackView.swift
//  newsly
//
//  Card stack with dismissed-set pattern (always shows index 0 of visible cards)
//

import SwiftUI

struct CardStackView: View {
    let groups: [NewsGroup]
    let onDismiss: (String) async -> Void

    // Track dismissed group IDs for immediate visual feedback
    @State private var dismissedGroupIds: Set<String> = []

    // Visible groups = not read AND not dismissed
    private var visibleGroups: [NewsGroup] {
        groups.filter { group in
            !group.isRead && !dismissedGroupIds.contains(group.id)
        }
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .top) {
                if visibleGroups.isEmpty {
                    // Empty state - all cards swiped away
                    VStack(spacing: 16) {
                        Image(systemName: "newspaper")
                            .font(.largeTitle)
                            .foregroundColor(.secondary)
                        Text("No more news")
                            .font(.title3)
                            .foregroundColor(.secondary)
                        Text("Pull to refresh")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    // Background placeholder cards (show 3 cards behind top card)
                    ForEach(1..<min(4, visibleGroups.count), id: \.self) { offset in
                        PlaceholderCard(
                            scale: 1.0 - CGFloat(offset) * 0.03,
                            yOffset: CGFloat(offset) * 6
                        )
                        .zIndex(Double(10 - offset))
                    }

                    // Top card (always at index 0 of visible groups)
                    SwipeableCard(onDismiss: {
                        handleCardDismissed()
                    }) {
                        NewsGroupCard(group: visibleGroups[0])
                    }
                    .id(visibleGroups[0].id)  // Use group ID for stable identity
                    .zIndex(100)
                }
            }
            .frame(maxHeight: geometry.size.height - 40)  // Leave space for tab bar
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
        .animation(.easeInOut(duration: 0.2), value: visibleGroups.count)
        .onChange(of: groups.count) { oldCount, newCount in
            // Clean up dismissed IDs that are no longer in the groups array
            // (they've been marked as read or removed from backend)
            if newCount < oldCount {
                let currentGroupIds = Set(groups.map { $0.id })
                dismissedGroupIds = dismissedGroupIds.intersection(currentGroupIds)
            }

            // On refresh (count goes to 0 or significantly changes), clear dismissed set
            if newCount == 0 || abs(newCount - oldCount) > 10 {
                dismissedGroupIds.removeAll()
            }
        }
    }

    private func handleCardDismissed() {
        // Guard: ensure we have visible groups
        guard !visibleGroups.isEmpty else { return }

        let dismissedGroup = visibleGroups[0]

        // Mark as dismissed immediately (synchronous - instant visual feedback)
        dismissedGroupIds.insert(dismissedGroup.id)

        // Call async operations in background (backend update)
        Task {
            await onDismiss(dismissedGroup.id)
        }
    }
}
