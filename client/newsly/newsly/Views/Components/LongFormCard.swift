//
//  LongFormCard.swift
//  newsly
//
//  Hero image card for long-form content (articles/podcasts).
//

import SwiftUI

private enum LongFormCardDesign {
    static let imageHeight: CGFloat = 220
}

struct LongFormCard: View {
    let content: ContentSummary
    let playbackService: NarrationPlaybackService
    var isAudioSupported = false
    var isAudioPreparing = false
    var isAudioPlaying = false
    var isAudioControlVisible = false
    var audioTarget: NarrationTarget?
    var audioErrorMessage: String?
    var onMarkRead: (() -> Void)?
    var onToggleKnowledgeSave: (() -> Void)?
    var onDigDeeper: ((String) -> Void)?
    var onOpen: (() -> Void)?
    var onToggleAudio: (() -> Void)?

    @State private var heroWidth: CGFloat = 0
    @State private var markReadFeedbackTrigger = 0
    @State private var knowledgeSaveFeedbackTrigger = 0
    @State private var knowledgeSelectionFeedbackTrigger = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                onOpen?()
            } label: {
                heroSection
            }
            .buttonStyle(CardRegionButtonStyle())
            .accessibilityHidden(true)
            .overlay(alignment: .topTrailing) {
                if isAudioSupported {
                    audioActionButton
                        .padding(12)
                }
            }

            VStack(alignment: .leading, spacing: 0) {
                Button {
                    onOpen?()
                } label: {
                    VStack(alignment: .leading, spacing: 0) {
                        FeedListText(
                            content.displayTitle,
                            textColor: .readerBodyText,
                            font: .terracottaHeadlineLarge,
                            lineLimit: 3,
                            onDigDeeper: onDigDeeper
                        )
                        .padding(.bottom, 8)

                        if let summary = summaryText {
                            FeedListText(
                                summary,
                                textColor: .readerBodyText,
                                font: .readerSummaryBody,
                                lineLimit: 3,
                                onDigDeeper: onDigDeeper
                            )
                            .padding(.bottom, 12)
                        }
                    }
                }
                .buttonStyle(CardRegionButtonStyle())
                .accessibilityLabel(content.displayTitle)
                .accessibilityHint("Opens content")

                HStack {
                    Button {
                        onOpen?()
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: contentTypeIcon)
                                .font(.appSymbol(size: 13, weight: .medium))
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .frame(width: 18, height: 18)
                                .accessibilityHidden(true)

                            Text(footerMetadata)
                                .kicker(color: .platformLabel)
                                .lineLimit(1)
                                .truncationMode(.tail)
                        }
                    }
                    .buttonStyle(CardRegionButtonStyle())
                    .accessibilityLabel("\(contentTypeLabel), \(sourceLabel)")
                    .accessibilityHint("Opens content")

                    Spacer()

                    HStack(spacing: 12) {
                        Button {
                            markReadFeedbackTrigger += 1
                            onMarkRead?()
                        } label: {
                            Image(systemName: content.isRead ? "checkmark.circle.fill" : "checkmark.circle")
                                .font(.appSymbol(size: 20))
                                .foregroundStyle(Color.onSurfaceSecondary)
                                .contentTransition(.symbolEffect(.replace))
                                .animation(AppMotion.subtle, value: content.isRead)
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.plain)
                        .contentShape(Rectangle())
                        .accessibilityIdentifier("long.action.mark_read.\(content.id)")
                        .accessibilityLabel(content.isRead ? "Marked as read" : "Mark as read")

                        Button {
                            if content.isSavedToKnowledge {
                                knowledgeSelectionFeedbackTrigger += 1
                            } else {
                                knowledgeSaveFeedbackTrigger += 1
                            }
                            onToggleKnowledgeSave?()
                        } label: {
                            KnowledgeSaveIcon(
                                isSaved: content.isSavedToKnowledge,
                                unsavedColor: Color.onSurfaceSecondary,
                                badgeColor: Color.brandPrimary
                            )
                            .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.plain)
                        .contentShape(Rectangle())
                        .accessibilityIdentifier("long.action.knowledge.\(content.id)")
                        .accessibilityLabel(content.isSavedToKnowledge ? "Remove from Knowledge" : "Save to Knowledge")
                    }
                }
                .padding(.top, 4)

                if isAudioControlVisible {
                    NarrationPlaybackControlRow(
                        playbackService: playbackService,
                        target: audioTarget,
                        isPreparing: isAudioPreparing,
                        cornerRadius: CornerRadius.nestedControl,
                        onTogglePlayback: {
                            onToggleAudio?()
                        }
                    )
                    .padding(.top, 12)
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }

                if let audioErrorMessage {
                    Text(audioErrorMessage)
                        .font(.appCaption)
                        .foregroundStyle(Color.statusDestructive)
                        .lineLimit(2)
                        .padding(.top, 8)
                        .transition(.opacity)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 14)
            .padding(.bottom, 14)
            .offset(y: CardMetrics.textOverlapOffset)
            .padding(.bottom, CardMetrics.textOverlapOffset)
            .background(
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0),
                        .init(color: Color.surfaceSecondary, location: 0.25),
                        .init(color: Color.surfaceSecondary, location: 1.0),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .offset(y: CardMetrics.textOverlapOffset)
                .padding(.bottom, CardMetrics.textOverlapOffset)
            )
            .animation(AppMotion.panel, value: isAudioControlVisible)
            .animation(AppMotion.subtle, value: audioErrorMessage)
        }
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: CardMetrics.cardCornerRadius, style: .continuous))
        .appShadow(.editorialCard)
        .sensoryFeedback(.impact(weight: .light), trigger: markReadFeedbackTrigger)
        .sensoryFeedback(.success, trigger: knowledgeSaveFeedbackTrigger)
        .sensoryFeedback(.selection, trigger: knowledgeSelectionFeedbackTrigger)
    }

    private var heroSection: some View {
        Color.clear
            .frame(height: LongFormCardDesign.imageHeight)
            .overlay {
                heroImage
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .clipped()
            .onGeometryChange(for: CGFloat.self) { proxy in
                proxy.size.width
            } action: { width in
                heroWidth = width
            }
            .overlay(alignment: .bottom) {
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0.0),
                        .init(color: Color.surfaceSecondary.opacity(0.35), location: 0.62),
                        .init(color: Color.surfaceSecondary, location: 1.0),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .contentShape(Rectangle())
    }

    private var audioActionButton: some View {
        Button {
            onToggleAudio?()
        } label: {
            ZStack {
                Circle()
                    .fill(Color.surfacePrimary.opacity(0.92))
                    .frame(width: 38, height: 38)
                    .appShadow(.floating)

                if isAudioPreparing {
                    ProgressView()
                        .controlSize(.small)
                        .tint(Color.terracottaPrimary)
                } else {
                    Image(systemName: isAudioPlaying ? "pause.fill" : "play.fill")
                        .font(.appSymbol(size: 14, weight: .bold))
                        .foregroundStyle(Color.terracottaPrimary)
                        .contentTransition(.symbolEffect(.replace))
                        .animation(AppMotion.subtle, value: isAudioPlaying)
                        .offset(x: isAudioPlaying ? 0 : 1)
                }
            }
            .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .contentShape(Rectangle())
        .disabled(isAudioPreparing)
        .accessibilityIdentifier("long.action.audio.\(content.id)")
        .accessibilityLabel(isAudioPlaying ? "Pause audio discussion" : "Play audio discussion")
    }

    @ViewBuilder
    private var heroImage: some View {
        // Loading waits for the measured card width so each hero is decoded once
        // at its final size; a guessed width (e.g. UIScreen bounds) over-decodes
        // on iPad and would trigger a second decode when the real width differs.
        if heroWidth <= 0 {
            placeholderGradient
        } else {
            heroImageContent(
                targetSize: CGSize(width: heroWidth, height: LongFormCardDesign.imageHeight)
            )
        }
    }

    @ViewBuilder
    private func heroImageContent(targetSize: CGSize) -> some View {
        let imageUrl = content.imageUrl.flatMap { ServerImageURL.resolve($0) }
        let thumbnailUrl = content.thumbnailUrl.flatMap { ServerImageURL.resolve($0) }
        if let imageUrl {
            CachedAsyncImage(url: imageUrl, thumbnailUrl: thumbnailUrl, targetSize: targetSize) { image in
                image
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } placeholder: {
                placeholderGradient
            }
        } else if let thumbnailUrl {
            CachedAsyncImage(url: thumbnailUrl, targetSize: targetSize) { image in
                image
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } placeholder: {
                placeholderGradient
            }
        } else {
            placeholderGradient
        }
    }

    private var placeholderGradient: some View {
        LinearGradient(
            colors: [Color.surfaceContainer, Color.surfaceContainerHigh],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
        .overlay(
            Image(systemName: contentTypeIcon)
                .font(.appSymbol(size: 40))
                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.3))
        )
    }

    private var summaryText: String? {
        guard content.contentType != .news else { return nil }
        return content.keyTakeawayDisplayText
    }

    private var sourceLabel: String {
        if let source = content.source, !source.isEmpty {
            return source.uppercased()
        }
        if let platform = content.platform, !platform.isEmpty {
            return platform.uppercased()
        }
        return content.contentType.displayName.uppercased()
    }

    private var footerMetadata: String {
        var parts = [sourceLabel]
        if let time = content.relativeTimeDisplay {
            parts.append(time.uppercased())
        }
        return parts.joined(separator: "  •  ")
    }

    private var contentTypeIcon: String {
        switch content.contentType {
        case .article:
            return "doc.text"
        case .podcast:
            return "headphones"
        default:
            return "doc"
        }
    }

    private var contentTypeLabel: String {
        switch content.contentType {
        case .article:
            return "Article"
        case .podcast:
            return "Podcast"
        default:
            return "Content"
        }
    }

}

// Press feedback for the card's open regions. Opacity-only: scaling a single
// region of the card would read as fragmented, and the regions stay separate
// accessibility elements so feed e2e flows can target the inner actions.
private struct CardRegionButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.82 : 1)
            .animation(AppMotion.press, value: configuration.isPressed)
    }
}
