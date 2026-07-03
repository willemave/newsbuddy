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

struct ContentDetailNavigationContext {
    let initialContentId: Int
    let initialContentType: APIContentType?
    let contentIds: [Int]
    let surface: ContentDetailNavigationSurface

    init(
        initialContentId: Int,
        initialContentType: APIContentType?,
        contentIds: [Int],
        surface: ContentDetailNavigationSurface
    ) {
        self.initialContentId = initialContentId
        self.initialContentType = initialContentType
        self.contentIds = contentIds.contains(initialContentId) ? contentIds : [initialContentId]
        self.surface = surface
    }

    var initialIndex: Int {
        contentIds.firstIndex(of: initialContentId) ?? contentIds.startIndex
    }

    func contentId(at index: Int) -> Int? {
        guard contentIds.indices.contains(index) else { return nil }
        return contentIds[index]
    }
}
