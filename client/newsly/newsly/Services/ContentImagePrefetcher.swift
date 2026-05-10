//
//  ContentImagePrefetcher.swift
//  newsly
//
//  Created by Assistant on 5/10/26.
//

import Foundation

enum ContentImagePrefetcher {
    static func urls(for content: ContentSummary, includeFullImage: Bool = true) -> [URL] {
        var urls: [URL] = []

        if let thumbnailURL = content.thumbnailUrl.flatMap(buildImageURL) {
            urls.append(thumbnailURL)
        }

        if includeFullImage, let imageURL = content.imageUrl.flatMap(buildImageURL) {
            urls.append(imageURL)
        }

        return urls.uniqued()
    }

    static func prefetch(_ content: ContentSummary, includeFullImage: Bool = true) {
        prefetch(contents: [content], includeFullImage: includeFullImage)
    }

    static func prefetch(contents: [ContentSummary], includeFullImage: Bool = true) {
        let imageURLs = contents.flatMap { urls(for: $0, includeFullImage: includeFullImage) }
        guard !imageURLs.isEmpty else { return }

        Task.detached(priority: .utility) {
            await ImageCacheService.shared.prefetch(urls: imageURLs)
        }
    }

    private static func buildImageURL(from urlString: String) -> URL? {
        if urlString.hasPrefix("http://") || urlString.hasPrefix("https://") {
            return URL(string: urlString)
        }

        let baseURL = AppSettings.shared.baseURL
        let fullURL = urlString.hasPrefix("/") ? baseURL + urlString : baseURL + "/" + urlString
        return URL(string: fullURL)
    }
}

private extension Array where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
