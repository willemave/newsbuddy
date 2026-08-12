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
    let onOpenExternal: (URL) -> Void
    let onShare: () -> Void
    let readerTransitionNamespace: Namespace.ID?
    let onOpenReader: () -> Void
    let onDownloadMore: () -> Void
    let onConvertLinkedArticle: () -> Void
    let onToggleKnowledgeSave: () -> Void
    let onPodcastAudio: () -> Void
    let onPodcastAudioSpeed: (NarrationPlaybackSpeedOption) -> Void
    let onOpenKnowledgeActions: () -> Void

    var body: some View {
        HStack(spacing: 0) {
            if let externalURL {
                Button {
                    onOpenExternal(externalURL)
                } label: {
                    actionIcon("safari")
                }
                .buttonStyle(.plain)
                .detailActionBarSegment()
                .accessibilityIdentifier("content.action.open_external")
                .accessibilityLabel("Open article")
            }

            Button(action: onShare) {
                actionIcon("square.and.arrow.up")
            }
            .detailActionBarSegment()
            .accessibilityIdentifier("content.action.share")

            if canShowReader {
                Button(action: onOpenReader) {
                    if isLoadingReaderBody {
                        ProgressView()
                            .scaleEffect(0.8)
                            .frame(width: 44, height: 44)
                    } else {
                        actionIcon("doc.richtext")
                    }
                }
                .detailActionBarSegment()
                .matchedContentZoomSource(id: content.id, namespace: readerTransitionNamespace)
                .accessibilityIdentifier("content.action.reader")
                .accessibilityLabel("Read full article")
            }

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
                actionIcon("books.vertical.fill")
            }
            .detailActionBarSegment()
            .accessibilityIdentifier("content.action.knowledge_actions")
            .accessibilityLabel("Knowledge actions")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: 44)
        .textSelection(.disabled)
    }

    @ViewBuilder
    private func actionIcon(_ icon: String, color: Color = .readerBodyText) -> some View {
        Image(systemName: icon)
            .font(.appSymbol(size: 20, weight: .regular))
            .foregroundColor(overlaid ? .white : color)
            .appShadow(overlaid ? .overlayText : .none)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
    }

    @ViewBuilder
    private func knowledgeActionIcon(isSaved: Bool) -> some View {
        let unsavedColor: Color = overlaid ? .white : .readerBodyText
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
            actionIcon("speaker.wave.3.fill", color: .readerBodyText)
        } else {
            actionIcon("speaker.wave.2")
        }
    }
}

private extension View {
    func detailActionBarSegment() -> some View {
        self
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
    }
}
