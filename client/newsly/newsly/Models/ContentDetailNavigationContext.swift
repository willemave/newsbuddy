//
//  ContentDetailNavigationContext.swift
//  newsly
//

import Foundation

enum ContentDetailNavigationSurface: String, Codable, Hashable {
    case direct
    case fastNews = "fast_news"
    case longForm = "long_form"
    case knowledge
    case contentList = "content_list"
    case recentlyRead = "recently_read"
    case savedLibrary = "saved_library"
    case search
    case newsGroup = "news_group"
    case briefing
}

enum ContentDetailScrollTarget: String, Codable, Hashable {
    case comments
}

struct ContentDetailNavigationContext {
    let initialContentId: Int
    let initialContentType: APIContentType?
    let contentIds: [Int]
    let surface: ContentDetailNavigationSurface
    let initialScrollTarget: ContentDetailScrollTarget?

    init(
        initialContentId: Int,
        initialContentType: APIContentType?,
        contentIds: [Int],
        surface: ContentDetailNavigationSurface,
        initialScrollTarget: ContentDetailScrollTarget? = nil
    ) {
        self.initialContentId = initialContentId
        self.initialContentType = initialContentType
        self.contentIds = contentIds.contains(initialContentId) ? contentIds : [initialContentId]
        self.surface = surface
        self.initialScrollTarget = initialScrollTarget
    }

    var initialIndex: Int {
        contentIds.firstIndex(of: initialContentId) ?? contentIds.startIndex
    }

    func contentId(at index: Int) -> Int? {
        guard contentIds.indices.contains(index) else { return nil }
        return contentIds[index]
    }
}
