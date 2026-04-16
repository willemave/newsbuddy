//
//  CouncilBranchTabs.swift
//  newsly
//

import SwiftUI

struct CouncilBranchTabs: View {
    let candidates: [CouncilCandidate]
    let activeChildSessionId: Int?
    let selectingChildSessionId: Int?
    let hasSelectionTimedOut: Bool
    let onSelect: (CouncilCandidate) -> Void
    let onCancelSelection: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(candidates) { candidate in
                        CouncilCandidateTab(
                            candidate: candidate,
                            isSelected: activeChildSessionId == candidate.childSessionId,
                            isActive: activeChildSessionId == candidate.childSessionId,
                            isSelecting: selectingChildSessionId == candidate.childSessionId,
                            isInteractionDisabled: selectingChildSessionId != nil
                        ) {
                            onSelect(candidate)
                        }
                    }
                }
                .padding(.vertical, 2)
            }

            if hasSelectionTimedOut {
                CouncilSelectionTimeoutBanner(onCancel: onCancelSelection)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .accessibilityIdentifier("knowledge.council_switcher")
    }
}

private struct CouncilSelectionTimeoutBanner: View {
    let onCancel: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Label("Still switching perspectives", systemImage: "clock.badge.exclamationmark")
                .font(.terracottaBodySmall)
                .foregroundStyle(Color.onSurfaceSecondary)

            Spacer(minLength: 0)

            Button("Cancel", action: onCancel)
                .font(.terracottaBodySmall.weight(.semibold))
                .foregroundStyle(Color.statusDestructive)
                .buttonStyle(.plain)
                .accessibilityIdentifier("knowledge.council_cancel_selection")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color.surfaceSecondary.opacity(0.9), in: Capsule())
    }
}

private struct CouncilCandidateTab: View {
    let candidate: CouncilCandidate
    let isSelected: Bool
    let isActive: Bool
    let isSelecting: Bool
    let isInteractionDisabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(candidate.personaName)
                    .lineLimit(1)

                if isSelecting {
                    ProgressView()
                        .controlSize(.small)
                } else if isActive {
                    Image(systemName: "checkmark.circle.fill")
                }
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(isSelected ? Color.chatAccent : Color.onSurfaceSecondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(
                Capsule()
                    .fill(isSelected ? Color.chatAccent.opacity(0.14) : Color.surfaceSecondary.opacity(0.72))
            )
            .overlay(
                Capsule()
                    .stroke(
                        isSelected ? Color.chatAccent.opacity(0.32) : Color.outlineVariant.opacity(0.18),
                        lineWidth: 1
                    )
            )
        }
        .buttonStyle(.plain)
        .disabled(isInteractionDisabled)
        .opacity(isInteractionDisabled && !isSelecting ? 0.5 : 1)
        .accessibilityIdentifier("council.tab.\(candidate.childSessionId)")
        .accessibilityLabel(isActive ? "\(candidate.personaName), current branch" : candidate.personaName)
        .accessibilityHint("Switches the active council branch")
    }
}

#if DEBUG
#Preview("Council Branch Tabs") {
    CouncilBranchTabs(
        candidates: ChatPreviewFixtures.councilCandidates,
        activeChildSessionId: 101,
        selectingChildSessionId: 103,
        hasSelectionTimedOut: true,
        onSelect: { _ in },
        onCancelSelection: {}
    )
    .padding()
    .background(Color.surfacePrimary)
}
#endif
