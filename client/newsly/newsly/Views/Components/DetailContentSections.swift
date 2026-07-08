//
//  DetailContentSections.swift
//  newsly
//

import SwiftUI
import UIKit

struct DetailContentSections: View {
    let content: ContentDetail
    let contentBodyText: String?
    let inlineDiscussion: ContentDiscussion?
    let isSubscribingToFeed: Bool
    let feedSubscriptionSuccess: Bool
    let feedSubscriptionError: String?
    @Binding var isTranscriptExpanded: Bool
    let startTopicSession: (String) async throws -> ChatSessionSummary
    let onSummaryAppear: (_ section: String, _ bulletPointCount: Int, _ insightCount: Int) -> Void
    let onSubscribeToDetectedFeed: () -> Void
    let onOpenURL: (URL) -> Void
    let linkStateForLink: (String) -> LinkReadLaterState
    let onAddRelevantLink: (RelevantLink) -> Void
    let onOpenFullDiscussion: (URL) -> Void
    let onDigDeeper: (String) -> Void

    var body: some View {
        if (content.canSubscribe ?? false), let feed = content.detectedFeed {
            DetectedFeedCard(
                feed: feed,
                isSubscribing: isSubscribingToFeed,
                hasSubscribed: feedSubscriptionSuccess,
                subscriptionError: feedSubscriptionError,
                onSubscribe: onSubscribeToDetectedFeed
            )
            .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
            .padding(.top, 12)
        }

        DetailSummarySections(
            content: content,
            startTopicSession: startTopicSession,
            onSummaryAppear: onSummaryAppear
        )

        if let sourceMetadata = content.sourceMetadata {
            SourceMetadataSection(
                metadata: sourceMetadata,
                openURL: onOpenURL
            )
            .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
            .padding(.top, DetailContentSectionsDesign.sectionSpacing)
        }

        let relevantLinks = content.relevantLinks
        if content.contentType != .news, !relevantLinks.isEmpty {
            relevantLinksView(relevantLinks)
        }

        if content.contentType == .news {
            newsDetailsView
        }

        if let inlineDiscussion {
            CommunityDiscussionSummarySection(
                discussion: inlineDiscussion,
                onOpenComments: onOpenFullDiscussion,
                onOpenURL: onOpenURL
            )
            .id(ContentDetailScrollTarget.comments)
            .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
            .padding(.top, 16)
        }

        if content.contentType == .news, !relevantLinks.isEmpty {
            relevantLinksView(relevantLinks)
        }

        expandableBodySection
    }

    @ViewBuilder
    private var newsDetailsView: some View {
        if content.newsMetadata != nil {
            NewsItemDetailView(content: content)
                .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
                .padding(.top, DetailContentSectionsDesign.sectionSpacing)
        } else {
            VStack(alignment: .leading, spacing: 16) {
                ReaderSectionHeader("News Updates")
                Text("No news metadata available.")
                    .font(.appSubheadline)
                    .foregroundColor(Color.onSurfaceSecondary)
            }
            .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
            .padding(.top, DetailContentSectionsDesign.sectionSpacing)
        }
    }

    @ViewBuilder
    private var expandableBodySection: some View {
        if content.contentType != .news, let bodyText = contentBodyText {
            ExpandableSection(
                title: content.contentType == .podcast ? "Transcript" : "Full Article",
                icon: content.contentType == .podcast ? "text.alignleft" : "doc.text",
                isExpanded: $isTranscriptExpanded
            ) {
                markdownBody(bodyText)
            }
            .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
            .padding(.top, DetailContentSectionsDesign.sectionSpacing)
        } else if content.contentType == .podcast,
                  let podcastMetadata = content.podcastMetadata,
                  let transcript = podcastMetadata.transcript {
            ExpandableSection(
                title: "Transcript",
                icon: "text.alignleft",
                isExpanded: $isTranscriptExpanded
            ) {
                markdownBody(transcript)
            }
            .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
            .padding(.top, DetailContentSectionsDesign.sectionSpacing)
        } else if let fullMarkdown = content.fullMarkdown {
            ExpandableSection(
                title: content.contentType == .podcast ? "Transcript" : "Full Article",
                icon: "doc.text",
                isExpanded: $isTranscriptExpanded
            ) {
                markdownBody(fullMarkdown)
            }
            .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
            .padding(.top, DetailContentSectionsDesign.sectionSpacing)
        }
    }

    private func relevantLinksView(_ links: [RelevantLink]) -> some View {
        RelevantLinksSection(
            links: links,
            stateForLink: linkStateForLink,
            onOpenURL: onOpenURL,
            onAddToReadLater: onAddRelevantLink
        )
        .padding(.horizontal, DetailContentSectionsDesign.horizontalPadding)
        .padding(.top, DetailContentSectionsDesign.sectionSpacing)
    }

    private func markdownBody(_ markdown: String) -> some View {
        SelectableMarkdownView(
            markdown: markdown,
            textColor: .appReaderBodyText,
            baseFont: .appReaderBody,
            adjustsFontForContentSizeCategory: true,
            onDigDeeper: onDigDeeper
        )
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private enum DetailContentSectionsDesign {
    static let horizontalPadding: CGFloat = Spacing.appHorizontalMargin
    static let sectionSpacing: CGFloat = 20
}
