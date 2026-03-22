//
//  ChatSessionCard.swift
//  newsly
//

import SwiftUI

struct ChatSessionCard: View {
    let session: ChatSessionSummary

    /// Whether this session was recently active (within last 5 minutes)
    private var isRecentlyActive: Bool {
        guard let dateStr = session.lastMessageAt else { return false }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = formatter.date(from: dateStr)
        if date == nil {
            formatter.formatOptions = [.withInternetDateTime]
            date = formatter.date(from: dateStr)
        }
        guard let date else { return false }
        return Date().timeIntervalSince(date) < 300
    }

    private enum BadgeStyle {
        case thinking
        case ready
        case none
    }

    private var badgeStyle: BadgeStyle {
        if session.isProcessing { return .thinking }
        if !session.isProcessing && session.hasAnyMessages && isRecentlyActive { return .ready }
        return .none
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header row: title + badge + arrow
            HStack(spacing: 8) {
                Text(session.displayTitle)
                    .font(.terracottaHeadlineSmall)
                    .foregroundColor(.onSurface)
                    .lineLimit(1)

                Spacer()

                statusBadge

                Image(systemName: "arrow.right")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.onSurfaceSecondary)
            }

            // Preview row
            previewRow
        }
        .padding(14)
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.outlineVariant.opacity(0.3), lineWidth: 1)
        )
    }

    @ViewBuilder
    private var statusBadge: some View {
        switch badgeStyle {
        case .thinking:
            HStack(spacing: 4) {
                ProgressView()
                    .scaleEffect(0.5)
                Text("THINKING")
                    .font(.terracottaLabelSmall)
                    .tracking(0.5)
            }
            .foregroundColor(.onSurfaceSecondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Color.surfaceContainer)
            .cornerRadius(4)

        case .ready:
            Text("READY")
                .font(.terracottaLabelSmall)
                .tracking(0.5)
                .foregroundColor(.terracottaPrimary)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Color.terracottaPrimary.opacity(0.1))
                .cornerRadius(4)

        case .none:
            EmptyView()
        }
    }

    @ViewBuilder
    private var previewRow: some View {
        if let preview = session.lastMessagePreview, !preview.isEmpty {
            let role = session.lastMessageRole ?? "assistant"
            let prefix = role == "user" ? "You: " : "AI: "
            let prefixColor: Color = role == "user" ? .onSurface : .terracottaPrimary

            (Text(prefix).foregroundColor(prefixColor).fontWeight(.medium) +
             Text(preview).foregroundColor(.onSurfaceSecondary))
                .font(.terracottaBodyMedium)
                .lineLimit(2)
        } else if session.isEmptyFavorite, let summary = session.articleSummary, !summary.isEmpty {
            Text(summary)
                .font(.terracottaBodyMedium)
                .foregroundColor(.onSurfaceSecondary)
                .lineLimit(2)
        } else if let subtitle = session.displaySubtitle {
            Text(subtitle)
                .font(.terracottaBodyMedium)
                .foregroundColor(.onSurfaceSecondary)
                .lineLimit(2)
        }
    }
}
