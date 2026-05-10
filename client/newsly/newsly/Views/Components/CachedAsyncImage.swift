//
//  CachedAsyncImage.swift
//  newsly
//
//  Created by Assistant on 12/23/25.
//

import SwiftUI

/// A cached version of AsyncImage that uses ImageCacheService for memory and disk caching.
/// Supports progressive loading from thumbnail to full image.
struct CachedAsyncImage<Content: View, Placeholder: View>: View {
    let url: URL?
    let thumbnailUrl: URL?
    let scale: CGFloat
    @ViewBuilder let content: (Image) -> Content
    @ViewBuilder let placeholder: () -> Placeholder
    
    @State private var loadedImage: UIImage?
    @State private var thumbnailImage: UIImage?
    @State private var isLoading = false
    @State private var activeURLKey: String?
    
    init(
        url: URL?,
        thumbnailUrl: URL? = nil,
        scale: CGFloat = 1.0,
        @ViewBuilder content: @escaping (Image) -> Content,
        @ViewBuilder placeholder: @escaping () -> Placeholder
    ) {
        self.url = url
        self.thumbnailUrl = thumbnailUrl
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
        return "\(urlKey)|\(thumbKey)"
    }

    private func loadImage() async {
        await MainActor.run {
            let newKey = requestKey
            if activeURLKey != newKey {
                loadedImage = nil
                thumbnailImage = nil
                activeURLKey = newKey
            }
            isLoading = true
        }

        guard let url = url else {
            await MainActor.run {
                isLoading = false
            }
            return
        }

        if let cached = await ImageCacheService.shared.image(for: url) {
            if Task.isCancelled { return }
            await MainActor.run {
                withAnimation(.easeIn(duration: 0.15)) {
                    loadedImage = cached
                    isLoading = false
                }
            }
            return
        }

        if let thumbnailUrl = thumbnailUrl {
            if let thumbImage = await ImageCacheService.shared.image(
                for: thumbnailUrl,
                downloadIfMissing: true
            ) {
                if Task.isCancelled { return }
                await MainActor.run {
                    thumbnailImage = thumbImage
                }
            }
        }

        if Task.isCancelled { return }

        if let image = await ImageCacheService.shared.image(for: url, downloadIfMissing: true) {
            if Task.isCancelled { return }
            await MainActor.run {
                withAnimation(.easeIn(duration: 0.2)) {
                    loadedImage = image
                    isLoading = false
                }
            }
        } else {
            await MainActor.run {
                isLoading = false
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
        scale: CGFloat = 1.0,
        @ViewBuilder content: @escaping (Image) -> Content
    ) {
        self.init(
            url: url,
            thumbnailUrl: thumbnailUrl,
            scale: scale,
            content: content,
            placeholder: { ProgressView() }
        )
    }
}

extension CachedAsyncImage where Content == Image, Placeholder == ProgressView<EmptyView, EmptyView> {
    /// Creates a CachedAsyncImage that displays the image directly.
    init(url: URL?, thumbnailUrl: URL? = nil, scale: CGFloat = 1.0) {
        self.init(
            url: url,
            thumbnailUrl: thumbnailUrl,
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
