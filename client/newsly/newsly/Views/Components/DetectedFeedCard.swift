//
//  DetectedFeedCard.swift
//  newsly
//
//  Created by Claude on 12/20/25.
//

import SwiftUI

/// A card that shows when a feed is detected for the current content,
/// allowing the user to subscribe to it.
struct DetectedFeedCard: View {
    let feed: DetectedFeed
    let isSubscribing: Bool
    let hasSubscribed: Bool
    let subscriptionError: String?
    let onSubscribe: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: feed.systemIcon)
                    .font(.appTitle2)
                    .foregroundColor(.onSurfaceSecondary)

                VStack(alignment: .leading, spacing: 2) {
                    Text("Subscribe to \(feed.feedTypeName)")
                        .font(.appHeadline)

                    if let title = feed.title, !title.isEmpty {
                        Text(title)
                            .font(.appSubheadline)
                            .foregroundColor(Color.onSurfaceSecondary)
                            .lineLimit(1)
                    }
                }

                Spacer()

                if hasSubscribed {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.brandPrimary)
                        .font(.appTitle2)
                } else {
                    Button(action: onSubscribe) {
                        if isSubscribing {
                            ProgressView()
                                .scaleEffect(0.8)
                        } else {
                            Text("Subscribe")
                                .font(.appSubheadline.weight(.medium))
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isSubscribing)
                }
            }

            if hasSubscribed {
                Text("You'll now receive new content from this source")
                    .font(.appCaption)
                    .foregroundColor(.onSurfaceSecondary)
            } else if let error = subscriptionError {
                Text(error)
                    .font(.appCaption)
                    .foregroundColor(.statusDestructive)
            } else {
                Text("Get new content from this source automatically")
                    .font(.appCaption)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
        }
        .padding(16)
        .background(Color.surfaceSecondary)
        .cornerRadius(12)
    }
}

#Preview {
    VStack(spacing: 20) {
        DetectedFeedCard(
            feed: DetectedFeed(
                url: "https://example.substack.com/feed",
                type: "substack",
                title: "Example Newsletter",
                format: "rss"
            ),
            isSubscribing: false,
            hasSubscribed: false,
            subscriptionError: nil,
            onSubscribe: {}
        )

        DetectedFeedCard(
            feed: DetectedFeed(
                url: "https://example.com/podcast.rss",
                type: "podcast_rss",
                title: "Example Podcast",
                format: "rss"
            ),
            isSubscribing: true,
            hasSubscribed: false,
            subscriptionError: nil,
            onSubscribe: {}
        )

        DetectedFeedCard(
            feed: DetectedFeed(
                url: "https://example.com/feed.xml",
                type: "atom",
                title: nil,
                format: "atom"
            ),
            isSubscribing: false,
            hasSubscribed: true,
            subscriptionError: nil,
            onSubscribe: {}
        )
    }
    .padding()
}
