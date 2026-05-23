//
//  LongFormCard.swift
//  newsly
//
//  Hero image card for long-form content (articles/podcasts).
//

import SwiftUI
import UIKit

private enum LongFormCardDesign {
    static let imageHeight: CGFloat = 220
    static let headlineFont = UIFont(name: "Newsreader", size: 28) ?? UIFont.systemFont(ofSize: 28, weight: .regular)
    static let summaryFont = UIFont(name: "Inter", size: 14) ?? UIFont.systemFont(ofSize: 14, weight: .regular)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Color.clear
                .frame(height: LongFormCardDesign.imageHeight)
                .overlay {
                    heroImage
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .clipped()
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
                .overlay(alignment: .topTrailing) {
                    if isAudioSupported {
                        audioActionButton
                            .padding(12)
                    }
                }
                .contentShape(Rectangle())
                .onTapGesture {
                    onOpen?()
                }

            VStack(alignment: .leading, spacing: 0) {
                if let relativeTime = content.relativeTimeDisplay {
                    HStack(spacing: 8) {
                        Text(relativeTime)
                            .font(.terracottaBodySmall)
                            .foregroundStyle(Color.onSurfaceSecondary)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        onOpen?()
                    }
                    .padding(.bottom, 8)
                }

                SelectableText(
                    content.displayTitle,
                    textColor: content.isRead ? .appOnSurfaceSecondary : .appOnSurface,
                    font: headlineUIFont,
                    lineLimit: 3,
                    lineBreakMode: .byTruncatingTail,
                    onDigDeeper: onDigDeeper,
                    onTap: onOpen
                )
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 8)

                if let summary = summaryText {
                    SelectableText(
                        summary,
                        textColor: .appOnSurfaceSecondary,
                        font: summaryUIFont,
                        lineLimit: 3,
                        lineBreakMode: .byTruncatingTail,
                        onDigDeeper: onDigDeeper,
                        onTap: onOpen
                    )
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 12)
                }

                HStack {
                    HStack(spacing: 8) {
                        Image(systemName: contentTypeIcon)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .frame(width: 18, height: 18)
                            .accessibilityHidden(true)

                        Text(sourceLabel)
                            .font(.terracottaBodySmall)
                            .tracking(0.5)
                            .foregroundStyle(Color.onSurfaceSecondary)
                            .lineLimit(1)
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        onOpen?()
                    }
                    .accessibilityLabel("\(contentTypeLabel), \(sourceLabel)")

                    Spacer()

                    HStack(spacing: 12) {
                        Button {
                            onMarkRead?()
                        } label: {
                            Image(systemName: content.isRead ? "checkmark.circle.fill" : "checkmark.circle")
                                .font(.system(size: 20))
                                .foregroundStyle(content.isRead ? Color.onSurfaceSecondary.opacity(0.5) : Color.onSurfaceSecondary)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("long.action.mark_read.\(content.id)")

                        Button {
                            onToggleKnowledgeSave?()
                        } label: {
                            KnowledgeSaveIcon(
                                isSaved: content.isSavedToKnowledge,
                                unsavedColor: Color.onSurfaceSecondary
                            )
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("long.action.knowledge.\(content.id)")
                    }
                }
                .padding(.top, 4)

                if isAudioControlVisible {
                    NarrationPlaybackControlRow(
                        playbackService: playbackService,
                        target: audioTarget,
                        isPreparing: isAudioPreparing,
                        onTogglePlayback: {
                            onToggleAudio?()
                        }
                    )
                    .padding(.top, 12)
                }

                if let audioErrorMessage {
                    Text(audioErrorMessage)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .lineLimit(2)
                        .padding(.top, 8)
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
        }
        .background(Color.surfaceSecondary)
        .clipShape(RoundedRectangle(cornerRadius: CardMetrics.cardCornerRadius, style: .continuous))
        .shadow(color: Color.onSurface.opacity(0.06), radius: 32, x: 0, y: 8)
    }

    private var audioActionButton: some View {
        Button {
            onToggleAudio?()
        } label: {
            ZStack {
                Circle()
                    .fill(Color.surfacePrimary.opacity(0.92))
                    .frame(width: 38, height: 38)
                    .shadow(color: Color.onSurface.opacity(0.16), radius: 10, x: 0, y: 4)

                if isAudioPreparing {
                    ProgressView()
                        .controlSize(.small)
                        .tint(Color.terracottaPrimary)
                } else {
                    Image(systemName: isAudioPlaying ? "pause.fill" : "play.fill")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(Color.terracottaPrimary)
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(isAudioPreparing)
        .accessibilityIdentifier("long.action.audio.\(content.id)")
        .accessibilityLabel(isAudioPlaying ? "Pause audio discussion" : "Play audio discussion")
    }

    @ViewBuilder
    private var heroImage: some View {
        let imageUrl = content.imageUrl.flatMap { buildImageURL(from: $0) }
        let thumbnailUrl = content.thumbnailUrl.flatMap { buildImageURL(from: $0) }
        if let imageUrl {
            CachedAsyncImage(url: imageUrl, thumbnailUrl: thumbnailUrl) { image in
                image
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } placeholder: {
                placeholderGradient
            }
        } else if let thumbnailUrl {
            CachedAsyncImage(url: thumbnailUrl) { image in
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
                .font(.system(size: 40))
                .foregroundStyle(Color.onSurfaceSecondary.opacity(0.3))
        )
    }

    private var summaryText: String? {
        guard content.contentTypeEnum != .news else { return nil }
        if let oneLine = content.feedPreview?.oneLine, !oneLine.isEmpty {
            return oneLine
        }
        if let summary = content.summaryDisplayText {
            return summary
        }
        return nil
    }

    private var headlineUIFont: UIFont {
        LongFormCardDesign.headlineFont
    }

    private var summaryUIFont: UIFont {
        LongFormCardDesign.summaryFont
    }

    private var sourceLabel: String {
        if let source = content.source, !source.isEmpty {
            return source.uppercased()
        }
        if let platform = content.platform, !platform.isEmpty {
            return platform.uppercased()
        }
        if let typeName = content.contentTypeEnum?.displayName {
            return typeName.uppercased()
        }
        return "NEWSLY"
    }

    private var contentTypeIcon: String {
        switch content.contentTypeEnum {
        case .article:
            return "doc.text"
        case .podcast:
            return "headphones"
        default:
            return "doc"
        }
    }

    private var contentTypeLabel: String {
        switch content.contentTypeEnum {
        case .article:
            return "Article"
        case .podcast:
            return "Podcast"
        default:
            return "Content"
        }
    }

    private func buildImageURL(from urlString: String) -> URL? {
        if urlString.hasPrefix("http://") || urlString.hasPrefix("https://") {
            return URL(string: urlString)
        }
        let baseURL = AppSettings.shared.baseURL
        let fullURL = urlString.hasPrefix("/") ? baseURL + urlString : baseURL + "/" + urlString
        return URL(string: fullURL)
    }
}
