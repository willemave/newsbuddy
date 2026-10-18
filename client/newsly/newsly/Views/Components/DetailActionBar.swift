//
//  DetailActionBar.swift
//  newsly
//

import SwiftUI

struct DetailActionBar: View {
    let content: ContentDetail
    let overlaid: Bool
    let externalURL: URL?
    let canShowReader: Bool
    let isLoadingReaderBody: Bool
    let isConverting: Bool
    let supportsPodcastAudio: Bool
    let isPodcastAudioLoading: Bool
    let isPodcastAudioActive: Bool
    let podcastAudioAccessibilityLabel: String
    let onShare: () -> Void
    let readerTransitionNamespace: Namespace.ID?
    let onOpenReader: () -> Void
    let onDownloadMore: () -> Void
    let onConvertLinkedArticle: () -> Void
    let onToggleKnowledgeSave: () -> Void
    let onCreateDeck: () -> Void
    let onPodcastAudio: () -> Void
    let onPodcastAudioSpeed: (NarrationPlaybackSpeedOption) -> Void
    let onOpenKnowledgeActions: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            // One reading entry; the reader opens on the original page when no text exists.
            if canShowReader || externalURL != nil {
                Button(action: onOpenReader) {
                    if isLoadingReaderBody {
                        ProgressView()
                            .scaleEffect(0.8)
                            .frame(width: 44, height: 44)
                    } else {
                        actionIcon("doc.richtext")
                    }
                }
                .buttonStyle(.plain)
                .detailActionBarSegment()
                .matchedContentZoomSource(id: content.id, namespace: readerTransitionNamespace)
                .accessibilityIdentifier("content.action.reader")
                .accessibilityLabel(canShowReader ? readerAccessibilityLabel : "Open original")
            }

            Button(action: onShare) {
                actionIcon("square.and.arrow.up", verticalOffset: -1)
            }
            .detailActionBarSegment()
            .accessibilityIdentifier("content.action.share")

            if content.contentType == .article || content.contentType == .podcast {
                Button(action: onDownloadMore) {
                    actionIcon("tray.and.arrow.down")
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.download_more")
                .accessibilityLabel("Download more from this series")
            }

            if content.contentType == .news {
                Button(action: onConvertLinkedArticle) {
                    if isConverting {
                        ProgressView()
                            .scaleEffect(0.8)
                            .frame(width: 44, height: 44)
                    } else {
                        knowledgeActionIcon(isSaved: false)
                    }
                }
                .disabled(isConverting)
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.convert")
                .accessibilityLabel("Save linked article to Knowledge")
            }

            if content.contentType != .news {
                Button(action: onToggleKnowledgeSave) {
                    knowledgeActionIcon(isSaved: content.isSavedToKnowledge)
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.knowledge")
                .accessibilityLabel(content.isSavedToKnowledge ? "Remove from Knowledge" : "Save to Knowledge")
            }

            if content.contentType == .article || content.contentType == .podcast {
                Button(action: onCreateDeck) {
                    actionIcon("rectangle.on.rectangle")
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.create_deck")
                .accessibilityLabel("Create Learning Deck")
            }

            if supportsPodcastAudio {
                NarrationPressButton(
                    isDisabled: isPodcastAudioLoading,
                    accessibilityLabel: podcastAudioAccessibilityLabel,
                    onTap: onPodcastAudio,
                    onSelectPlaybackSpeed: onPodcastAudioSpeed
                ) {
                    podcastAudioIcon
                }
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.podcast_audio")
            }

            Button(action: onOpenKnowledgeActions) {
                buddyActionIcon()
            }
            .detailActionBarSegment()
            .accessibilityIdentifier("content.action.knowledge_actions")
            .accessibilityLabel("Knowledge actions")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: 44)
        .textSelection(.disabled)
    }

    private var readerAccessibilityLabel: String {
        content.contentType == .podcast ? "Read transcript" : "Read full article"
    }

    /// The ask-the-buddy action, matching `actionIcon`'s metrics so the left-packed bar
    /// keeps a steady rhythm. Kept separate because the buddy is an asset, not an SF
    /// Symbol, and carries its own color rather than the bar's tint.
    @ViewBuilder
    private func buddyActionIcon() -> some View {
        Image("BuddyMark")
            .resizable()
            .renderingMode(.original)
            .scaledToFit()
            .frame(width: 22, height: 22)
            .appShadow(overlaid ? .overlayText : .none)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }

    @ViewBuilder
    private func actionIcon(
        _ icon: String,
        verticalOffset: CGFloat = 0
    ) -> some View {
        Image(systemName: icon)
            .font(.appSymbol(size: 20, weight: .regular))
            .foregroundColor(overlaid ? .white : .onSurface)
            .appShadow(overlaid ? .overlayText : .none)
            .offset(y: verticalOffset)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }

    @ViewBuilder
    private func knowledgeActionIcon(isSaved: Bool) -> some View {
        let unsavedColor: Color = overlaid ? .white : .onSurface
        KnowledgeSaveIcon(
            isSaved: isSaved,
            savedColor: .brandPrimary,
            unsavedColor: unsavedColor,
            badgeColor: unsavedColor,
            badgeForegroundColor: .surfacePrimary
        )
        .contentTransition(.symbolEffect(.replace))
        .animation(AppMotion.subtle, value: isSaved)
        .appShadow(overlaid ? .overlayText : .none)
        .frame(width: 44, height: 44)
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private var podcastAudioIcon: some View {
        if isPodcastAudioLoading {
            ProgressView()
                .scaleEffect(0.8)
                .frame(width: 44, height: 44)
        } else if isPodcastAudioActive {
            actionIcon("pause.fill")
        } else {
            actionIcon("play.fill")
        }
    }
}

private extension View {
    func detailActionBarSegment() -> some View {
        self
            .contentShape(Rectangle())
    }
}
