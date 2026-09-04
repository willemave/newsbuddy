//
//  SubmissionStatusRow.swift
//  newsly
//
//  Created by Assistant on 1/14/26.
//

import SwiftUI

struct SubmissionStatusRow: View {
    let submission: SubmissionStatusItem

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                // Status icon
                statusIcon
                    .frame(width: RowMetrics.smallThumbnailSize, height: RowMetrics.smallThumbnailSize)
                    .background(statusColor.opacity(0.10))
                    .clipShape(RoundedRectangle(cornerRadius: 10))

                VStack(alignment: .leading, spacing: 6) {
                    // Title — primary visual weight
                    Text(submission.displayTitle)
                        .font(.listTitle)
                        .foregroundStyle(Color.onSurface)
                        .lineLimit(2)

                    // Domain + date on one line
                    HStack(spacing: 4) {
                        if let host = URL(string: submission.url)?.host {
                            Text(host)
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                        if let date = submission.statusDateDisplay {
                            Text("·")
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceTertiary)
                            Text(date)
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                    }

                    // Status badge row
                    HStack(spacing: 6) {
                        TextBadge(text: submission.statusLabel, color: statusColor)

                        if submission.isLearningDeck {
                            TextBadge(text: "Learning Deck", color: .brandPrimary, style: .outlined)
                        }

                        if submission.isSelfSubmission {
                            TextBadge(text: "Submitted", color: .brandPrimary, style: .outlined)
                        }
                    }

                    // Status detail — muted, with icon
                    if let detail = submission.statusDetailText {
                        HStack(alignment: .top, spacing: 4) {
                            Image(systemName: submission.isError ? "info.circle" : "checkmark.circle")
                                .font(.appCaption2)
                                .foregroundStyle(statusColor)
                                .padding(.top, 1)
                            Text(detail)
                                .font(.appCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .lineLimit(2)
                        }
                    }
                }

                Spacer(minLength: 0)
            }
            .padding(.vertical, Spacing.rowVertical)
            .padding(.horizontal, Spacing.rowHorizontal)

            Divider()
                .padding(.leading, Spacing.rowHorizontal + RowMetrics.smallThumbnailSize + 12)
        }
    }

    private var statusIcon: some View {
        Image(systemName: statusIconName)
            .font(.appSymbol(size: 16, weight: .medium))
            .foregroundStyle(statusColor)
    }

    private var statusIconName: String {
        if submission.isFeedSubscription {
            switch submission.effectiveOutcome {
            case .subscribed:
                return "checkmark.circle.fill"
            case .already_subscribed:
                return "checkmark.circle"
            case .feed_not_found, .feed_fetch_failed, .feed_subscription_failed, .failed:
                return "exclamationmark.triangle.fill"
            case .processing:
                return "arrow.triangle.2.circlepath"
            case .queued:
                return "clock.fill"
            default:
                return submission.detectedFeed?.systemIcon ?? "antenna.radiowaves.left.and.right"
            }
        }

        if submission.isLearningDeck {
            switch submission.effectiveOutcome {
            case .completed:
                return "rectangle.on.rectangle.fill"
            case .failed:
                return "exclamationmark.triangle.fill"
            case .processing:
                return "arrow.triangle.2.circlepath"
            case .queued:
                return "clock.fill"
            default:
                return "rectangle.on.rectangle"
            }
        }

        switch submission.effectiveOutcome {
        case .failed:
            return "exclamationmark.triangle.fill"
        case .skipped:
            return "forward.fill"
        case .processing:
            return "arrow.triangle.2.circlepath"
        case .completed:
            return "checkmark.circle.fill"
        case .no_action:
            return "arrow.uturn.forward.circle.fill"
        case .queued:
            return "clock.fill"
        default:
            return "questionmark.circle.fill"
        }
    }

    private var statusColor: Color {
        switch submission.effectiveOutcome {
        case .failed, .feed_not_found, .feed_fetch_failed, .feed_subscription_failed:
            return .statusDestructive
        case .skipped:
            return .onSurfaceSecondary
        case .subscribed, .already_subscribed, .completed:
            return .statusActive
        case .no_action:
            return .brandPrimary
        case .processing:
            return .brandPrimary
        case .queued:
            return .onSurfaceSecondary
        default:
            return .onSurfaceSecondary
        }
    }

}

#Preview {
    SubmissionStatusRow(
        submission: SubmissionStatusItem(
            id: 1,
            contentType: .article,
            url: "https://example.com",
            sourceUrl: nil,
            title: "Example submission",
            status: .processing,
            errorMessage: nil,
            createdAt: "2025-01-01T12:00:00Z",
            processedAt: nil,
            submittedVia: "share_sheet",
            isSelfSubmission: true
        )
    )
    .padding()
}
