//
//  SubmissionDetailView.swift
//  newsly
//
//  Created by Assistant on 1/15/26.
//

import SwiftUI

struct SubmissionDetailView: View {
    let submission: SubmissionStatusItem

    var body: some View {
        List {
            Section(header: Text("Status")) {
                HStack {
                    Text("State")
                    Spacer()
                    Text(submission.statusLabel)
                        .foregroundStyle(statusColor)
                }

                if let date = submission.statusDateDisplay {
                    HStack {
                        Text("Last updated")
                        Spacer()
                        Text(date)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }

                if let error = submission.errorDisplayText {
                        Text(error)
                            .foregroundStyle(Color.statusDestructive)
                }

                if !submission.isError, let detail = submission.statusDetailText {
                    Text(detail)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }
            }

            Section(header: Text("Details")) {
                if let title = submission.title, !title.isEmpty {
                    HStack {
                        Text("Title")
                        Spacer()
                        Text(title)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }

                HStack {
                    Text("Type")
                    Spacer()
                    Text(submission.contentType.capitalized)
                        .foregroundStyle(Color.onSurfaceSecondary)
                }

                if let submittedVia = submission.submittedVia, !submittedVia.isEmpty {
                    HStack {
                        Text("Submitted via")
                        Spacer()
                        Text(submittedVia.replacingOccurrences(of: "_", with: " ").capitalized)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                }
            }

            if submission.isFeedSubscription {
                Section(header: Text("Feed")) {
                    if let feedTitle = submission.detectedFeed?.title, !feedTitle.isEmpty {
                        DetailValueRow(label: "Title", value: feedTitle)
                    }

                    if let feedType = feedTypeDisplay {
                        DetailValueRow(label: "Feed type", value: feedType)
                    }

                    if let status = submission.feedSubscription?.status {
                        DetailValueRow(label: "Subscription", value: humanize(status))
                    }

                    if let configId = submission.feedSubscription?.configId {
                        DetailValueRow(label: "Config ID", value: String(configId))
                    }

                    if let initialDownload = submission.feedSubscription?.initialDownload {
                        DetailValueRow(
                            label: "Initial download",
                            value: initialDownloadDisplay(initialDownload)
                        )
                    }

                    if let feedUrl = submission.feedSubscription?.feedUrl ?? submission.detectedFeed?.url {
                        LinkRow(label: "Feed URL", value: feedUrl)
                    }
                }
            }

            Section(header: Text("Links")) {
                LinkRow(label: "URL", value: submission.url)

                if let sourceUrl = submission.sourceUrl, sourceUrl != submission.url {
                    LinkRow(label: "Source URL", value: sourceUrl)
                }
            }
        }
        .navigationTitle(submission.displayTitle)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var statusColor: Color {
        switch submission.effectiveOutcome {
        case "failed", "skipped", "feed_not_found", "feed_fetch_failed", "feed_subscription_failed":
            return .statusDestructive
        case "subscribed", "already_subscribed", "completed":
            return .statusActive
        default:
            return .onSurfaceSecondary
        }
    }

    private var feedTypeDisplay: String? {
        if let detectedFeed = submission.detectedFeed {
            return detectedFeed.feedTypeName
        }
        if let feedType = submission.feedSubscription?.feedType {
            return humanize(feedType)
        }
        return nil
    }

    private func initialDownloadDisplay(_ initialDownload: SubmissionFeedInitialDownload) -> String {
        let status = humanize(initialDownload.status ?? "unknown")
        guard let saved = initialDownload.saved else {
            return status
        }
        return "\(status), \(saved) saved"
    }

    private func humanize(_ value: String) -> String {
        value.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

#Preview {
    NavigationStack {
        SubmissionDetailView(
            submission: SubmissionStatusItem(
                id: 1,
                contentType: "podcast",
                url: "https://example.com/episode",
                sourceUrl: "https://example.com/source",
                title: "Example Episode",
                status: "failed",
                errorMessage: "No audio URL found",
                createdAt: "2025-01-01T12:00:00Z",
                processedAt: "2025-01-01T12:05:00Z",
                submittedVia: "share_sheet",
                isSelfSubmission: true
            )
        )
    }
}

private struct LinkRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.appCaption)
                .foregroundStyle(Color.onSurfaceSecondary)
            if let url = URL(string: value) {
                Link(destination: url) {
                    Text(value)
                        .font(.appFootnote)
                        .lineLimit(2)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("\(label): \(value)")
            } else {
                Text(value)
                    .font(.appFootnote)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            }
        }
        .textSelection(.enabled)
    }
}

private struct DetailValueRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .foregroundStyle(Color.onSurfaceSecondary)
                .multilineTextAlignment(.trailing)
        }
        .textSelection(.enabled)
    }
}
