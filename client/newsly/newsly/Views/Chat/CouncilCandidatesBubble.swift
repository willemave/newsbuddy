//
//  CouncilCandidatesBubble.swift
//  newsly
//

import SwiftUI
import UIKit

struct CouncilCandidatesBubble: View {
    let message: ChatMessage
    let textColor: UIColor
    let retryingChildSessionId: Int?
    let onRetryCandidate: (CouncilCandidate) -> Void

    private var candidates: [CouncilCandidate] {
        message.councilCandidates.sorted { $0.order < $1.order }
    }

    private var activeCandidate: CouncilCandidate? {
        if let activeChildSessionId,
           let candidate = candidates.first(where: { $0.childSessionId == activeChildSessionId }) {
            return candidate
        }
        return candidates.first
    }

    private var activeChildSessionId: Int? {
        message.activeCouncilChildSessionId ?? candidates.first?.childSessionId
    }

    var body: some View {
        if let activeCandidate {
            CouncilCandidateCard(
                candidate: activeCandidate,
                textColor: textColor,
                isActive: activeChildSessionId == activeCandidate.childSessionId,
                isRetrying: retryingChildSessionId == activeCandidate.childSessionId,
                onRetry: {
                    onRetryCandidate(activeCandidate)
                }
            )
            .transition(.opacity.combined(with: .move(edge: .bottom)))
        }
    }
}

private struct CouncilCandidateCard: View {
    let candidate: CouncilCandidate
    let textColor: UIColor
    let isActive: Bool
    let isRetrying: Bool
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(candidate.personaName)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Color.onSurface)

                    if isActive {
                        Text("Current branch")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(Color.chatAccent)
                    } else {
                        Text("Use the branch switcher below to change perspectives")
                            .font(.caption2)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }

                Spacer(minLength: 0)

                if isActive {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(Color.chatAccent)
                }
            }

            switch candidate.status {
            case "processing":
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Thinking")
                        .font(.callout)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            case "failed":
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(Color.statusDestructive.opacity(0.8))
                        Text("This perspective could not be generated.")
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    .font(.callout)

                    Button(action: onRetry) {
                        HStack(spacing: 8) {
                            if isRetrying {
                                ProgressView()
                                    .controlSize(.small)
                            } else {
                                Image(systemName: "arrow.clockwise")
                            }
                            Text(isRetrying ? "Retrying" : "Retry Voice")
                        }
                        .font(.terracottaBodySmall.weight(.semibold))
                        .foregroundStyle(Color.statusDestructive)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Color.statusDestructive.opacity(0.1), in: Capsule())
                        .overlay {
                            Capsule()
                                .stroke(Color.statusDestructive.opacity(0.2), lineWidth: 1)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(isRetrying)
                    .accessibilityIdentifier("knowledge.council_retry_voice")
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            default:
                SelectableMarkdownView(
                    markdown: candidate.content,
                    textColor: textColor,
                    baseFont: .preferredFont(forTextStyle: .callout)
                )
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(
            UnevenRoundedRectangle(
                topLeadingRadius: 4,
                bottomLeadingRadius: 16,
                bottomTrailingRadius: 16,
                topTrailingRadius: 16
            )
            .fill(Color.surfaceContainer)
        )
        .overlay(
            UnevenRoundedRectangle(
                topLeadingRadius: 4,
                bottomLeadingRadius: 16,
                bottomTrailingRadius: 16,
                topTrailingRadius: 16
            )
            .stroke(
                Color.chatAccent.opacity(0.35),
                lineWidth: 1
            )
        )
    }
}

#if DEBUG
#Preview("Council Candidates Bubble") {
    CouncilCandidatesBubble(
        message: ChatPreviewFixtures.councilMessage,
        textColor: UIColor(Color.onSurface),
        retryingChildSessionId: nil,
        onRetryCandidate: { _ in }
    )
    .padding()
    .background(Color.surfacePrimary)
}
#endif
