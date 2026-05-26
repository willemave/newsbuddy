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
                                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.5))
                            Text(date)
                                .font(.listCaption)
                                .foregroundStyle(Color.onSurfaceSecondary)
                        }
                    }

                    // Status badge row
                    HStack(spacing: 6) {
                        TextBadge(text: submission.statusLabel, color: statusColor)

                        if submission.isSelfSubmission {
                            TextBadge(text: "Submitted", color: .terracottaPrimary, style: .outlined)
                        }
                    }

                    // Error — muted, with icon
                    if let error = submission.errorDisplayText {
                        HStack(alignment: .top, spacing: 4) {
                            Image(systemName: "info.circle")
                                .font(.caption2)
                                .foregroundStyle(statusColor.opacity(0.7))
                                .padding(.top, 1)
                            Text(error)
                                .font(.caption)
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
            .font(.system(size: 16, weight: .medium))
            .foregroundStyle(statusColor)
    }

    private var statusIconName: String {
        switch submission.status.lowercased() {
        case "failed":
            return "exclamationmark.triangle.fill"
        case "skipped":
            return "forward.fill"
        case "processing":
            return "arrow.triangle.2.circlepath"
        case "completed":
            return "checkmark.circle.fill"
        case "new", "pending":
            return "clock.fill"
        default:
            return "questionmark.circle.fill"
        }
    }

    private var statusColor: Color {
        switch submission.status.lowercased() {
        case "failed":
            return .statusDestructive.opacity(0.9)
        case "skipped":
            return .brandTertiary.opacity(0.9)
        case "processing":
            return .terracottaPrimary
        case "completed":
            return .statusActive
        case "new", "pending":
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
            contentType: "article",
            url: "https://example.com",
            sourceUrl: nil,
            title: "Example submission",
            status: "processing",
            errorMessage: nil,
            createdAt: "2025-01-01T12:00:00Z",
            processedAt: nil,
            submittedVia: "share_sheet",
            isSelfSubmission: true
        )
    )
    .padding()
}
