//
//  DetailHeroHeader.swift
//  newsly
//

import SwiftUI

struct DetailHeroHeader<ActionBar: View, PodcastControls: View>: View {
    let content: ContentDetail
    let reduceMotion: Bool
    let showsPodcastPlaybackControls: Bool
    @ViewBuilder let actionBar: (_ overlaid: Bool) -> ActionBar
    @ViewBuilder let podcastPlaybackControls: () -> PodcastControls

    @State private var selectedImageAsset: DetailHeroImageAsset?

    var body: some View {
        if let imageURL = heroImageURL(for: content) {
            imageHero(imageURL: imageURL, thumbnailURL: heroThumbnailURL(for: content))
        } else {
            textOnlyHero
        }
    }

    private func imageHero(imageURL: URL, thumbnailURL: URL?) -> some View {
        ZStack(alignment: .bottomLeading) {
            GeometryReader { geo in
                let minY = geo.frame(in: .named("detailScroll")).minY
                let isOverscroll = minY > 0
                let scrolled = max(-minY, 0)
                let rate = reduceMotion ? 0 : DetailHeroHeaderDesign.parallaxRate
                let parallaxShift = scrolled * rate
                let stretch = (isOverscroll && !reduceMotion) ? minY : 0
                let extraHeight = geo.size.height * rate
                let imageHeight = geo.size.height + geo.safeAreaInsets.top + stretch + extraHeight

                Button {
                    selectedImageAsset = DetailHeroImageAsset(
                        imageURL: imageURL,
                        thumbnailURL: thumbnailURL
                    )
                } label: {
                    CachedAsyncImage(
                        url: imageURL,
                        thumbnailUrl: thumbnailURL,
                        targetSize: CGSize(width: geo.size.width, height: imageHeight)
                    ) { image in
                        image
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                            .frame(width: geo.size.width, height: imageHeight)
                            .clipped()
                    } placeholder: {
                        Rectangle()
                            .fill(Color.surfaceTertiary)
                            .frame(width: geo.size.width, height: imageHeight)
                            .overlay(ProgressView())
                    }
                }
                .buttonStyle(.plain)
                .offset(y: -geo.safeAreaInsets.top - parallaxShift + (isOverscroll ? -minY : 0))
            }

            imageScrim

            VStack(alignment: .leading, spacing: 8) {
                titleText(color: .white)
                    .appShadow(.strongOverlayText)

                metadataRow(
                    primaryColor: .white.opacity(0.9),
                    secondaryColor: .white.opacity(0.8),
                    separatorColor: .white.opacity(0.5)
                )
                .appShadow(.overlayText)

                actionBar(true)
                    .padding(.top, 2)

                if showsPodcastPlaybackControls {
                    podcastPlaybackControls()
                        .padding(.top, 2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, DetailHeroHeaderDesign.headerHorizontalPadding)
            .padding(.bottom, 10)
        }
        .frame(height: DetailHeroHeaderDesign.parallaxHeroHeight)
        .mask(Rectangle().padding(.top, -200))
        .fullScreenCover(item: $selectedImageAsset) { asset in
            FullImageView(imageURL: asset.imageURL, thumbnailURL: asset.thumbnailURL)
        }
    }

    private var imageScrim: some View {
        LinearGradient(
            gradient: Gradient(stops: [
                .init(color: .clear, location: 0.0),
                .init(color: .clear, location: 0.20),
                .init(color: Color.black.opacity(0.35), location: 0.45),
                .init(color: Color.black.opacity(0.75), location: 0.72),
                .init(color: Color.black.opacity(0.88), location: 0.90),
                .init(color: Color.surfacePrimary, location: 1.0)
            ]),
            startPoint: .top,
            endPoint: .bottom
        )
        .allowsHitTesting(false)
    }

    private var textOnlyHero: some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer()
                .frame(height: textOnlyHeaderTopSpacer)

            VStack(alignment: .leading, spacing: 8) {
                titleText(color: Color.onSurface)

                metadataRow(
                    primaryColor: .onSurfaceSecondary,
                    secondaryColor: .onSurfaceSecondary,
                    separatorColor: Color.onSurfaceSecondary.opacity(0.4)
                )
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, DetailHeroHeaderDesign.headerHorizontalPadding)
            .padding(.top, DetailHeroHeaderDesign.textOnlyTitleTopPadding)
            .padding(.bottom, 6)

            actionBar(false)
                .padding(
                    .horizontal,
                    DetailHeroHeaderDesign.headerHorizontalPadding - DetailHeroHeaderDesign.actionIconOpticalInset
                )
                .padding(.top, 2)

            if showsPodcastPlaybackControls {
                podcastPlaybackControls()
                    .padding(.horizontal, DetailHeroHeaderDesign.headerHorizontalPadding)
                    .padding(.top, 4)
            }
        }
    }

    private func titleText(color: Color) -> some View {
        Text(content.displayTitle)
            .font(.appSerif(size: 20, relativeTo: .title3, weight: .medium))
            .fontWeight(.medium)
            .foregroundColor(color)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("content.detail.title.\(content.id)")
    }

    private func metadataRow(
        primaryColor: Color,
        secondaryColor: Color,
        separatorColor: Color
    ) -> some View {
        HStack(spacing: 6) {
            HStack(spacing: 4) {
                Image(systemName: contentTypeIcon)
                    .font(.appCaption2)
                Text(content.detailTypeLabel)
                    .font(.appCaption)
                    .fontWeight(.medium)
            }
            .foregroundColor(primaryColor)

            if let source = content.source {
                Text("·")
                    .foregroundColor(separatorColor)
                Text(source)
                    .font(.appCaption)
                    .foregroundColor(secondaryColor)
            }

            Text("·")
                .foregroundColor(separatorColor)

            ContentTimestampText(
                rawValue: content.primaryTimestamp,
                style: .detailMeta,
                fallback: "Recent"
            )
            .font(.appCaption)
            .foregroundColor(secondaryColor)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(detailMetadataAccessibilityLabel)
    }

    private var textOnlyHeaderTopSpacer: CGFloat {
        content.contentType == .news
            ? DetailHeroHeaderDesign.textOnlyNewsHeaderTopSpacer
            : DetailHeroHeaderDesign.textOnlyStandardHeaderTopSpacer
    }

    private var detailMetadataAccessibilityLabel: String {
        [
            content.detailTypeLabel,
            content.source,
            ContentTimestampFormatter.text(from: content.primaryTimestamp, style: .detailMeta) ?? "Recent"
        ]
        .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
        .joined(separator: ", ")
    }

    private var contentTypeIcon: String {
        switch content.contentType {
        case .article: return "doc.text"
        case .podcast: return "headphones"
        case .news: return "newspaper"
        case .insight_report, .unknown, .unknownRaw: return "doc.text"
        }
    }

    private func heroImageURL(for content: ContentDetail) -> URL? {
        guard let imageURLString = content.imageUrl,
              !imageURLString.isEmpty,
              content.contentType != .news else {
            return nil
        }
        return ServerImageURL.resolve(imageURLString)
    }

    private func heroThumbnailURL(for content: ContentDetail) -> URL? {
        content.thumbnailUrl.flatMap { ServerImageURL.resolve($0) }
    }
}

private struct DetailHeroImageAsset: Identifiable {
    let imageURL: URL
    let thumbnailURL: URL?

    var id: String { imageURL.absoluteString }
}

private enum DetailHeroHeaderDesign {
    static let headerHorizontalPadding: CGFloat = Spacing.appHorizontalMargin
    static let parallaxHeroHeight: CGFloat = 260
    static let parallaxRate: CGFloat = 0.25
    static let textOnlyTitleTopPadding: CGFloat = 18
    static let textOnlyNewsHeaderTopSpacer: CGFloat = 42
    static let textOnlyStandardHeaderTopSpacer: CGFloat = 48
    static let actionIconOpticalInset: CGFloat = 12
}
