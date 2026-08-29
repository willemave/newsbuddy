//
//  CachedAsyncImage.swift
//  newsly
//
//  Created by Assistant on 12/23/25.
//

import SwiftUI

enum ImageRequestSizing {
    private static let pixelBucket = 128

    static func targetPixelSize(for targetSize: CGSize?, scale: CGFloat) -> Int? {
        guard let targetSize else { return nil }
        let maxPointDimension = max(targetSize.width, targetSize.height)
        guard maxPointDimension.isFinite, maxPointDimension > 0 else { return nil }

        let requestedPixels = max(1, Int((maxPointDimension * scale).rounded(.up)))
        return ((requestedPixels + pixelBucket - 1) / pixelBucket) * pixelBucket
    }
}

/// A cached version of AsyncImage that uses ImageCacheService for memory and disk caching.
/// Supports progressive loading from thumbnail to full image.
struct CachedAsyncImage<Content: View, Placeholder: View>: View {
    let url: URL?
    let thumbnailUrl: URL?
    let cacheIdentifier: String?
    let scale: CGFloat
    let targetSize: CGSize?
    @ViewBuilder let content: (Image) -> Content
    @ViewBuilder let placeholder: () -> Placeholder
    
    @State private var loadedImage: UIImage?
    @State private var thumbnailImage: UIImage?
    @State private var activeURLKey: String?
    
    init(
        url: URL?,
        thumbnailUrl: URL? = nil,
        cacheIdentifier: String? = nil,
        targetSize: CGSize? = nil,
        scale: CGFloat = 2.0,
        @ViewBuilder content: @escaping (Image) -> Content,
        @ViewBuilder placeholder: @escaping () -> Placeholder
    ) {
        self.url = url
        self.thumbnailUrl = thumbnailUrl
        self.cacheIdentifier = cacheIdentifier
        self.targetSize = targetSize
        self.scale = scale
        self.content = content
        self.placeholder = placeholder
    }
    
    var body: some View {
        Group {
            if let image = loadedImage {
                content(Image(uiImage: image))
            } else if let thumbnail = thumbnailImage {
                content(Image(uiImage: thumbnail))
            } else {
                placeholder()
            }
        }
        .task(id: requestKey) {
            await loadImage()
        }
    }

    private var requestKey: String {
        let urlKey = url?.absoluteString ?? "nil"
        let thumbKey = thumbnailUrl?.absoluteString ?? "nil"
        let sizeKey = targetPixelSize.map(String.init) ?? "original"
        return "\(urlKey)|\(thumbKey)|\(cacheIdentifier ?? "default")|\(sizeKey)"
    }

    private var presentationKey: String {
        let sourceKey = cacheIdentifier ?? url?.absoluteString ?? "nil"
        let sizeKey = targetPixelSize.map(String.init) ?? "original"
        return "\(sourceKey)|\(sizeKey)"
    }

    private var targetPixelSize: Int? {
        ImageRequestSizing.targetPixelSize(for: targetSize, scale: scale)
    }

    private func loadImage() async {
        await MainActor.run {
            let newKey = presentationKey
            if activeURLKey != newKey {
                loadedImage = nil
                thumbnailImage = nil
                activeURLKey = newKey
            }
        }

        guard let url = url else { return }

        let targetPixelSize = targetPixelSize

        if let cached = await ImageCacheService.shared.image(
            for: url,
            targetPixelSize: targetPixelSize,
            cacheIdentifier: cacheIdentifier
        ) {
            if Task.isCancelled { return }
            await MainActor.run {
                loadedImage = cached
            }
            return
        }

        if let thumbnailUrl = thumbnailUrl {
            if let thumbImage = await ImageCacheService.shared.image(
                for: thumbnailUrl,
                downloadIfMissing: true,
                targetPixelSize: targetPixelSize,
                cacheIdentifier: cacheIdentifier.map { "\($0).thumbnail" }
            ) {
                if Task.isCancelled { return }
                await MainActor.run {
                    thumbnailImage = thumbImage
                }
            }
        }

        if Task.isCancelled { return }

        if let image = await ImageCacheService.shared.image(
            for: url,
            downloadIfMissing: true,
            targetPixelSize: targetPixelSize,
            cacheIdentifier: cacheIdentifier
        ) {
            if Task.isCancelled { return }
            await MainActor.run {
                loadedImage = image
            }
        }
    }
}

// MARK: - Convenience Initializers

extension CachedAsyncImage where Placeholder == ProgressView<EmptyView, EmptyView> {
    /// Creates a CachedAsyncImage with a default ProgressView placeholder.
    init(
        url: URL?,
        thumbnailUrl: URL? = nil,
        cacheIdentifier: String? = nil,
        targetSize: CGSize? = nil,
        scale: CGFloat = 2.0,
        @ViewBuilder content: @escaping (Image) -> Content
    ) {
        self.init(
            url: url,
            thumbnailUrl: thumbnailUrl,
            cacheIdentifier: cacheIdentifier,
            targetSize: targetSize,
            scale: scale,
            content: content,
            placeholder: { ProgressView() }
        )
    }
}

extension CachedAsyncImage where Content == Image, Placeholder == ProgressView<EmptyView, EmptyView> {
    /// Creates a CachedAsyncImage that displays the image directly.
    init(
        url: URL?,
        thumbnailUrl: URL? = nil,
        cacheIdentifier: String? = nil,
        targetSize: CGSize? = nil,
        scale: CGFloat = 2.0
    ) {
        self.init(
            url: url,
            thumbnailUrl: thumbnailUrl,
            cacheIdentifier: cacheIdentifier,
            targetSize: targetSize,
            scale: scale,
            content: { $0 },
            placeholder: { ProgressView() }
        )
    }
}

// MARK: - Preview

#Preview {
    VStack(spacing: 20) {
        CachedAsyncImage(
            url: URL(string: "https://example.com/image.png")
        ) { image in
            image
                .resizable()
                .aspectRatio(contentMode: .fill)
                .frame(width: 100, height: 100)
                .clipShape(RoundedRectangle(cornerRadius: 8))
        } placeholder: {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.gray.opacity(0.3))
                .frame(width: 100, height: 100)
                .overlay(ProgressView())
        }
    }
    .padding()
}
