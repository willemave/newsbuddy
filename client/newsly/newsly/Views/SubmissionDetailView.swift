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
                        .foregroundStyle(submission.isError ? .red : .secondary)
                }

                if let date = submission.statusDateDisplay {
                    HStack {
                        Text("Last updated")
                        Spacer()
                        Text(date)
                            .foregroundStyle(.secondary)
                    }
                }

                if let error = submission.errorDisplayText {
                    Text(error)
                        .foregroundStyle(.red)
                }
            }

            Section(header: Text("Details")) {
                if let title = submission.title, !title.isEmpty {
                    HStack {
                        Text("Title")
                        Spacer()
                        Text(title)
                            .foregroundStyle(.secondary)
                    }
                }

                HStack {
                    Text("Type")
                    Spacer()
                    Text(submission.contentType.capitalized)
                        .foregroundStyle(.secondary)
                }

                if let submittedVia = submission.submittedVia, !submittedVia.isEmpty {
                    HStack {
                        Text("Submitted via")
                        Spacer()
                        Text(submittedVia.replacingOccurrences(of: "_", with: " ").capitalized)
                            .foregroundStyle(.secondary)
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
                .font(.caption)
                .foregroundStyle(.secondary)
            if let url = URL(string: value) {
                Link(value, destination: url)
                    .font(.footnote)
            } else {
                Text(value)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .textSelection(.enabled)
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
